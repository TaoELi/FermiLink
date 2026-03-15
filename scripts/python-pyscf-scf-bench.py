#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import resource
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml


def _peak_rss_mb() -> float:
    scale = 1024.0 if sys.platform != "darwin" else 1024.0 * 1024.0
    return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / scale


def _to_builtin(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, tuple):
        return [_to_builtin(item) for item in value]
    if isinstance(value, list):
        return [_to_builtin(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_builtin(item) for key, item in value.items()}
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)


def _flatten_numbers(value: Any) -> list[float]:
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, list):
        flattened: list[float] = []
        for item in value:
            flattened.extend(_flatten_numbers(item))
        return flattened
    if isinstance(value, dict):
        flattened: list[float] = []
        for key in sorted(value):
            flattened.extend(_flatten_numbers(value[key]))
        return flattened
    return []


def _rms(values: Any) -> float:
    flattened = _flatten_numbers(values)
    if not flattened:
        return 0.0
    return math.sqrt(sum(value * value for value in flattened) / len(flattened))


def _weighted_median(weighted_values: list[tuple[float, float]]) -> float:
    if not weighted_values:
        return float("inf")
    sorted_values = sorted(weighted_values, key=lambda item: item[0])
    total_weight = sum(weight for _, weight in sorted_values)
    if total_weight <= 0:
        return sorted_values[-1][0]
    threshold = total_weight / 2.0
    running = 0.0
    for value, weight in sorted_values:
        running += max(0.0, weight)
        if running >= threshold:
            return value
    return sorted_values[-1][0]


def _geometric_mean(values: list[float]) -> float:
    if not values:
        return float("inf")
    logs: list[float] = []
    for value in values:
        if not isinstance(value, (int, float)):
            return float("inf")
        number = float(value)
        if not math.isfinite(number) or number <= 0.0:
            return float("inf")
        logs.append(math.log(number))
    return math.exp(sum(logs) / len(logs))


def _safe_positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _safe_non_negative_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _finite_number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        parsed = float(value)
        if math.isfinite(parsed):
            return parsed
    return None


def _load_benchmark(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Benchmark YAML must contain an object: {path}")
    return payload


def _method_and_family(case: dict[str, Any]) -> tuple[str, str]:
    scf_cfg = case.get("scf")
    scf_cfg = scf_cfg if isinstance(scf_cfg, dict) else {}
    method = str(scf_cfg.get("method") or "RHF").strip().upper()
    if method in {"RKS", "UKS"}:
        return method, "dft"
    if method in {"RHF", "UHF"}:
        return method, "hf"
    raise ValueError(f"Unsupported SCF method: {method}")


def _normalize_execution_profile(value: Any, *, threads: int) -> str:
    profile = str(value or "").strip().lower()
    if profile in {"smp", "multi", "multi_cpu", "multi-cpu"}:
        return "smp"
    if profile in {"single", "serial", "single_cpu", "single-cpu"}:
        return "single"
    return "smp" if threads > 1 else "single"


def _thread_profiles(benchmark: dict[str, Any]) -> dict[str, int]:
    profiles: dict[str, int] = {}
    raw_profiles = benchmark.get("thread_profiles")
    if isinstance(raw_profiles, dict):
        for name, raw_value in raw_profiles.items():
            profile_name = str(name or "").strip()
            if not profile_name:
                continue
            if isinstance(raw_value, dict):
                threads = _safe_positive_int(raw_value.get("threads"), default=0)
            else:
                threads = _safe_positive_int(raw_value, default=0)
            if threads > 0:
                profiles[profile_name] = threads
    if "single_cpu" not in profiles:
        profiles["single_cpu"] = _safe_positive_int(
            os.getenv("FERMILINK_PYSCF_SINGLE_THREADS"), default=1
        )
    if "smp_node" not in profiles:
        profiles["smp_node"] = _safe_positive_int(
            os.getenv("FERMILINK_PYSCF_SMP_THREADS"), default=16
        )
    return profiles


def _resolve_case_threads(
    case: dict[str, Any], benchmark: dict[str, Any]
) -> tuple[int, str]:
    explicit_threads = case.get("threads")
    if explicit_threads is not None:
        return _safe_positive_int(explicit_threads, default=1), "explicit"

    profile_name = str(case.get("thread_profile") or "").strip()
    profiles = _thread_profiles(benchmark)
    if profile_name and profile_name in profiles:
        return profiles[profile_name], profile_name

    execution_profile = _normalize_execution_profile(
        case.get("execution_profile"), threads=1
    )
    if execution_profile == "smp":
        fallback = profiles.get("smp_node", 16)
        threads = _safe_positive_int(
            os.getenv("FERMILINK_PYSCF_SMP_THREADS"), default=fallback
        )
        return threads, "smp_node"
    fallback = profiles.get("single_cpu", 1)
    threads = _safe_positive_int(
        os.getenv("FERMILINK_PYSCF_SINGLE_THREADS"), default=fallback
    )
    return threads, "single_cpu"


def _build_scf_method(case: dict[str, Any]):
    from pyscf import dft, gto, scf

    molecule = gto.M(
        atom=str(case.get("molecule") or ""),
        basis=str(case.get("basis") or ""),
        charge=int(case.get("charge") or 0),
        spin=int(case.get("spin") or 0),
        verbose=0,
        unit="Angstrom",
    )
    scf_cfg = case.get("scf")
    scf_cfg = scf_cfg if isinstance(scf_cfg, dict) else {}
    method, _family = _method_and_family(case)

    if method == "RHF":
        mf = scf.RHF(molecule)
    elif method == "UHF":
        mf = scf.UHF(molecule)
    elif method == "RKS":
        mf = dft.RKS(molecule)
    elif method == "UKS":
        mf = dft.UKS(molecule)
    else:
        raise ValueError(f"Unsupported SCF method: {method}")

    if method in {"RKS", "UKS"}:
        mf.xc = str(scf_cfg.get("xc") or "pbe")
        grids_level = scf_cfg.get("grids_level")
        if grids_level is not None:
            mf.grids.level = int(grids_level)

    for attr in (
        "max_cycle",
        "conv_tol",
        "conv_tol_grad",
        "diis_space",
        "level_shift",
        "direct_scf_tol",
    ):
        if attr in scf_cfg:
            setattr(mf, attr, scf_cfg[attr])
    mf.init_guess = str(scf_cfg.get("init_guess") or "minao")
    return mf


def _failed_case_payload(
    *,
    case_id: str,
    method: str,
    method_family: str,
    execution_profile: str,
    thread_profile: str,
    threads: int,
    pair_key: str,
    error: str,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "converged": False,
        "wall_seconds": float("inf"),
        "scf_iterations": 0,
        "total_energy_hartree": float("nan"),
        "dm_rms": float("nan"),
        "mo_energy_rms": float("nan"),
        "peak_rss_mb": _peak_rss_mb(),
        "density_matrix": [],
        "mo_energies": [],
        "method": method,
        "method_family": method_family,
        "execution_profile": execution_profile,
        "thread_profile": thread_profile,
        "threads": int(threads),
        "pair_key": pair_key,
        "error": str(error),
    }


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    case_id = str(case.get("id") or "case")
    method, method_family = _method_and_family(case)
    threads = _safe_positive_int(case.get("resolved_threads"), default=1)
    execution_profile = _normalize_execution_profile(
        case.get("execution_profile"), threads=threads
    )
    thread_profile = str(case.get("thread_profile") or "").strip() or "derived"
    pair_key = str(case.get("pair_key") or case_id).strip() or case_id

    try:
        mf = _build_scf_method(case)
        try:
            from pyscf import lib as pyscf_lib

            pyscf_lib.num_threads(threads)
        except Exception:
            pass
        started = time.perf_counter()
        energy = mf.kernel()
        wall_seconds = time.perf_counter() - started
        density_matrix = _to_builtin(mf.make_rdm1())
        mo_energies = _to_builtin(getattr(mf, "mo_energy", []))
        scf_iterations = (
            getattr(mf, "cycles", None)
            or getattr(mf, "iterations", None)
            or (
                mf.scf_summary.get("num_cycle")
                if isinstance(getattr(mf, "scf_summary", None), dict)
                else None
            )
            or 0
        )
        return {
            "id": case_id,
            "converged": bool(getattr(mf, "converged", False)),
            "wall_seconds": float(wall_seconds),
            "scf_iterations": int(scf_iterations),
            "total_energy_hartree": float(energy),
            "dm_rms": _rms(density_matrix),
            "mo_energy_rms": _rms(mo_energies),
            "peak_rss_mb": _peak_rss_mb(),
            "density_matrix": density_matrix,
            "mo_energies": mo_energies,
            "method": method,
            "method_family": method_family,
            "execution_profile": execution_profile,
            "thread_profile": thread_profile,
            "threads": int(threads),
            "pair_key": pair_key,
            "error": "",
        }
    except Exception as exc:
        return _failed_case_payload(
            case_id=case_id,
            method=method,
            method_family=method_family,
            execution_profile=execution_profile,
            thread_profile=thread_profile,
            threads=threads,
            pair_key=pair_key,
            error=str(exc),
        )


def _run_case_subprocess(case: dict[str, Any], *, threads: int) -> dict[str, Any]:
    case_id = str(case.get("id") or "case")
    method, method_family = _method_and_family(case)
    execution_profile = _normalize_execution_profile(
        case.get("execution_profile"), threads=threads
    )
    thread_profile = str(case.get("thread_profile") or "").strip() or "derived"
    pair_key = str(case.get("pair_key") or case_id).strip() or case_id
    child_case = dict(case)
    child_case["resolved_threads"] = int(threads)
    script_path = Path(__file__).resolve()
    command = [
        sys.executable,
        str(script_path),
        "--case-json",
        json.dumps(child_case, sort_keys=True),
    ]

    env = os.environ.copy()
    thread_text = str(int(threads))
    for var_name in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        env[var_name] = thread_text

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        message = stderr or stdout or f"child runner exited {completed.returncode}"
        return _failed_case_payload(
            case_id=case_id,
            method=method,
            method_family=method_family,
            execution_profile=execution_profile,
            thread_profile=thread_profile,
            threads=threads,
            pair_key=pair_key,
            error=message,
        )

    output = (completed.stdout or "").strip()
    if not output:
        return _failed_case_payload(
            case_id=case_id,
            method=method,
            method_family=method_family,
            execution_profile=execution_profile,
            thread_profile=thread_profile,
            threads=threads,
            pair_key=pair_key,
            error="child runner produced empty stdout",
        )

    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        try:
            parsed = json.loads(lines[-1]) if lines else {}
        except json.JSONDecodeError:
            parsed = {}
    if not isinstance(parsed, dict):
        return _failed_case_payload(
            case_id=case_id,
            method=method,
            method_family=method_family,
            execution_profile=execution_profile,
            thread_profile=thread_profile,
            threads=threads,
            pair_key=pair_key,
            error="child runner emitted invalid JSON payload",
        )
    parsed["method"] = str(parsed.get("method") or method)
    parsed["method_family"] = str(parsed.get("method_family") or method_family)
    parsed["execution_profile"] = _normalize_execution_profile(
        parsed.get("execution_profile"), threads=threads
    )
    parsed["thread_profile"] = str(parsed.get("thread_profile") or thread_profile)
    parsed["threads"] = int(_safe_positive_int(parsed.get("threads"), default=threads))
    parsed["pair_key"] = str(parsed.get("pair_key") or pair_key)
    parsed["id"] = str(parsed.get("id") or case_id)
    return parsed


def _load_incumbent_summary_metrics(
    benchmark: dict[str, Any],
) -> dict[str, float]:
    guardrails = benchmark.get("performance_guardrails")
    if not isinstance(guardrails, dict) or not bool(guardrails.get("enabled")):
        return {}
    raw_state_path = str(
        guardrails.get("state_json_path") or ".fermilink-optimize/state.json"
    ).strip()
    if not raw_state_path:
        return {}
    state_path = Path(raw_state_path).expanduser()
    if not state_path.is_absolute():
        state_path = (Path.cwd() / state_path).resolve()
    if not state_path.is_file():
        return {}
    try:
        state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    incumbent_metrics = state_payload.get("incumbent_metrics")
    if not isinstance(incumbent_metrics, dict):
        return {}
    summary = incumbent_metrics.get("summary_metrics")
    if not isinstance(summary, dict):
        return {}
    normalized: dict[str, float] = {}
    for key, value in summary.items():
        numeric = _finite_number(value)
        if numeric is not None:
            normalized[str(key)] = numeric
    return normalized


def _evaluate_performance_guardrails(
    *,
    benchmark: dict[str, Any],
    summary_metrics: dict[str, float],
    incumbent_summary: dict[str, float] | None = None,
) -> list[str]:
    guardrails = benchmark.get("performance_guardrails")
    if not isinstance(guardrails, dict) or not bool(guardrails.get("enabled")):
        return []
    incumbent = incumbent_summary or _load_incumbent_summary_metrics(benchmark)
    if not incumbent:
        return []

    errors: list[str] = []
    max_relative_regression = guardrails.get("max_relative_regression")
    if isinstance(max_relative_regression, dict):
        for metric_name, raw_limit in max_relative_regression.items():
            metric = str(metric_name or "").strip()
            if not metric:
                continue
            limit = _safe_non_negative_float(raw_limit)
            if limit is None:
                continue
            previous = _finite_number(incumbent.get(metric))
            if previous is None:
                continue
            current = _finite_number(summary_metrics.get(metric))
            if current is None:
                errors.append(f"missing metric `{metric}` for guardrail regression check")
                continue
            if current > previous * (1.0 + limit):
                errors.append(
                    f"`{metric}` regressed: {current:.12g} > {previous:.12g} * (1+{limit:.4g})"
                )

    max_relative_speedup_drop = guardrails.get("max_relative_speedup_drop")
    if isinstance(max_relative_speedup_drop, dict):
        for metric_name, raw_limit in max_relative_speedup_drop.items():
            metric = str(metric_name or "").strip()
            if not metric:
                continue
            limit = _safe_non_negative_float(raw_limit)
            if limit is None:
                continue
            previous = _finite_number(incumbent.get(metric))
            if previous is None or previous <= 0.0:
                continue
            current = _finite_number(summary_metrics.get(metric))
            if current is None:
                errors.append(f"missing metric `{metric}` for speedup guardrail check")
                continue
            if current < previous * (1.0 - limit):
                errors.append(
                    f"`{metric}` dropped: {current:.12g} < {previous:.12g} * (1-{limit:.4g})"
                )
    return errors


def _summary_weighted_median(
    rows: list[dict[str, Any]],
    *,
    key: str,
    selector: str | None = None,
    selector_value: str | None = None,
) -> float:
    weighted: list[tuple[float, float]] = []
    for row in rows:
        if selector is not None and str(row.get(selector)) != str(selector_value):
            continue
        value = _finite_number(row.get(key))
        weight = _finite_number(row.get("weight"))
        if value is None or weight is None or weight <= 0.0:
            continue
        weighted.append((value, weight))
    return _weighted_median(weighted)


def _paired_speedups(rows: list[dict[str, Any]]) -> list[float]:
    paired: dict[str, dict[str, float]] = {}
    for row in rows:
        pair_key = str(row.get("pair_key") or row.get("id") or "").strip()
        if not pair_key:
            continue
        profile = str(row.get("execution_profile") or "").strip().lower()
        wall = _finite_number(row.get("wall_seconds"))
        if wall is None or wall <= 0.0:
            continue
        bucket = paired.setdefault(pair_key, {})
        bucket[profile] = wall
    speedups: list[float] = []
    for bucket in paired.values():
        single = _finite_number(bucket.get("single"))
        smp = _finite_number(bucket.get("smp"))
        if single is None or smp is None or single <= 0.0 or smp <= 0.0:
            continue
        speedups.append(single / smp)
    return speedups


def _run_benchmark(benchmark: dict[str, Any]) -> dict[str, Any]:
    cases_raw = benchmark.get("cases")
    cases = cases_raw if isinstance(cases_raw, list) else []

    payload_cases: list[dict[str, Any]] = []
    converged_rows: list[dict[str, Any]] = []
    failures = 0
    for raw_case in cases:
        if not isinstance(raw_case, dict):
            continue
        case_id = str(raw_case.get("id") or "case")
        weight = _finite_number(raw_case.get("weight")) or 1.0
        threads, thread_profile = _resolve_case_threads(raw_case, benchmark)
        method, method_family = _method_and_family(raw_case)
        execution_profile = _normalize_execution_profile(
            raw_case.get("execution_profile"), threads=threads
        )
        pair_key = str(raw_case.get("pair_key") or case_id).strip() or case_id

        case_payload = _run_case_subprocess(raw_case, threads=threads)
        case_payload["method"] = str(case_payload.get("method") or method)
        case_payload["method_family"] = str(case_payload.get("method_family") or method_family)
        case_payload["execution_profile"] = _normalize_execution_profile(
            case_payload.get("execution_profile"), threads=threads
        )
        case_payload["thread_profile"] = str(
            case_payload.get("thread_profile") or thread_profile
        )
        case_payload["threads"] = int(
            _safe_positive_int(case_payload.get("threads"), default=threads)
        )
        case_payload["pair_key"] = str(case_payload.get("pair_key") or pair_key)
        payload_cases.append(case_payload)

        if bool(case_payload.get("converged")):
            converged_rows.append(
                {
                    "id": case_id,
                    "weight": weight,
                    "wall_seconds": _finite_number(case_payload.get("wall_seconds")),
                    "scf_iterations": _finite_number(case_payload.get("scf_iterations")),
                    "execution_profile": case_payload["execution_profile"],
                    "method_family": case_payload["method_family"],
                    "pair_key": case_payload["pair_key"],
                }
            )
        else:
            failures += 1

    weighted_median_wall_seconds = _summary_weighted_median(
        converged_rows, key="wall_seconds"
    )
    weighted_median_scf_iterations = _summary_weighted_median(
        converged_rows, key="scf_iterations"
    )
    single_wall = _summary_weighted_median(
        converged_rows,
        key="wall_seconds",
        selector="execution_profile",
        selector_value="single",
    )
    smp_wall = _summary_weighted_median(
        converged_rows,
        key="wall_seconds",
        selector="execution_profile",
        selector_value="smp",
    )
    hf_wall = _summary_weighted_median(
        converged_rows,
        key="wall_seconds",
        selector="method_family",
        selector_value="hf",
    )
    dft_wall = _summary_weighted_median(
        converged_rows,
        key="wall_seconds",
        selector="method_family",
        selector_value="dft",
    )
    profile_balanced = _geometric_mean([single_wall, smp_wall])
    method_balanced = _geometric_mean([hf_wall, dft_wall])
    robust_composite = _geometric_mean([single_wall, smp_wall, hf_wall, dft_wall])

    speedups = _paired_speedups(converged_rows)
    paired_geomean_speedup = _geometric_mean(speedups) if speedups else 0.0
    paired_min_speedup = min(speedups) if speedups else 0.0

    summary_metrics: dict[str, float] = {
        "weighted_median_wall_seconds": weighted_median_wall_seconds,
        "weighted_median_scf_iterations": weighted_median_scf_iterations,
        "single_weighted_median_wall_seconds": single_wall,
        "smp_weighted_median_wall_seconds": smp_wall,
        "hf_weighted_median_wall_seconds": hf_wall,
        "dft_weighted_median_wall_seconds": dft_wall,
        "profile_balanced_wall_seconds": profile_balanced,
        "method_balanced_wall_seconds": method_balanced,
        "robust_composite_wall_seconds": robust_composite,
        "paired_geomean_smp_speedup": paired_geomean_speedup,
        "paired_min_smp_speedup": paired_min_speedup,
        "paired_speedup_count": float(len(speedups)),
        "peak_rss_mb": max(
            (float(item.get("peak_rss_mb") or 0.0) for item in payload_cases),
            default=0.0,
        ),
        "total_failures": float(failures),
    }

    guardrail_errors = _evaluate_performance_guardrails(
        benchmark=benchmark,
        summary_metrics=summary_metrics,
    )
    summary_metrics["performance_guardrail_failures"] = float(len(guardrail_errors))
    summary_metrics["performance_guardrails_checked"] = (
        1.0 if benchmark.get("performance_guardrails") else 0.0
    )

    correctness_ok = failures == 0 and all(
        bool(item.get("converged")) for item in payload_cases
    )
    payload: dict[str, Any] = {
        "benchmark_id": str(benchmark.get("benchmark_id") or "benchmark"),
        "correctness_ok": correctness_ok,
        "summary_metrics": summary_metrics,
        "cases": payload_cases,
    }
    if guardrail_errors:
        payload["guardrail_errors"] = guardrail_errors
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a PySCF SCF benchmark suite.")
    parser.add_argument("--benchmark", help="Benchmark YAML path.")
    parser.add_argument("--emit-json", action="store_true", help="Emit JSON to stdout.")
    parser.add_argument("--case-json", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.case_json:
        case_payload = json.loads(args.case_json)
        if not isinstance(case_payload, dict):
            raise ValueError("--case-json must contain a JSON object.")
        payload = _run_case(case_payload)
        print(json.dumps(payload, sort_keys=True))
        return 0

    if not args.benchmark:
        raise ValueError("--benchmark is required unless --case-json is provided.")

    benchmark_path = Path(args.benchmark).expanduser().resolve()
    benchmark = _load_benchmark(benchmark_path)
    payload = _run_benchmark(benchmark)
    if args.emit_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
