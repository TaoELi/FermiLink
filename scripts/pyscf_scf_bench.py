#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import resource
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
        return float("nan")
    sorted_values = sorted(weighted_values, key=lambda item: item[0])
    total_weight = sum(weight for _, weight in sorted_values)
    threshold = total_weight / 2.0
    running = 0.0
    for value, weight in sorted_values:
        running += weight
        if running >= threshold:
            return value
    return sorted_values[-1][0]


def _load_benchmark(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Benchmark YAML must contain an object: {path}")
    return payload


def _build_scf_method(case: dict[str, Any]):
    from pyscf import gto, scf

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
    method = str(scf_cfg.get("method") or "RHF").strip().upper()
    if method == "UHF":
        mf = scf.UHF(molecule)
    else:
        mf = scf.RHF(molecule)
    for attr in ("max_cycle", "conv_tol", "conv_tol_grad", "diis_space", "level_shift"):
        if attr in scf_cfg:
            setattr(mf, attr, scf_cfg[attr])
    mf.init_guess = str(scf_cfg.get("init_guess") or "minao")
    return mf


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    case_id = str(case.get("id") or "case")
    try:
        mf = _build_scf_method(case)
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
            "error": "",
        }
    except Exception as exc:
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
            "error": str(exc),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a PySCF SCF benchmark suite.")
    parser.add_argument("--benchmark", required=True, help="Benchmark YAML path.")
    parser.add_argument("--emit-json", action="store_true", help="Emit JSON to stdout.")
    args = parser.parse_args(argv)

    benchmark_path = Path(args.benchmark).expanduser().resolve()
    benchmark = _load_benchmark(benchmark_path)
    cases_raw = benchmark.get("cases")
    cases = cases_raw if isinstance(cases_raw, list) else []

    weighted_times: list[tuple[float, float]] = []
    weighted_iterations: list[tuple[float, float]] = []
    payload_cases: list[dict[str, Any]] = []
    failures = 0
    for raw_case in cases:
        if not isinstance(raw_case, dict):
            continue
        case_payload = _run_case(raw_case)
        payload_cases.append(case_payload)
        weight = float(raw_case.get("weight") or 1.0)
        if bool(case_payload.get("converged")):
            weighted_times.append((float(case_payload["wall_seconds"]), weight))
            weighted_iterations.append((float(case_payload["scf_iterations"]), weight))
        else:
            failures += 1

    summary_metrics = {
        "weighted_median_wall_seconds": (
            _weighted_median(weighted_times) if weighted_times else float("inf")
        ),
        "weighted_median_scf_iterations": (
            _weighted_median(weighted_iterations)
            if weighted_iterations
            else float("inf")
        ),
        "peak_rss_mb": max(
            (float(item.get("peak_rss_mb") or 0.0) for item in payload_cases),
            default=0.0,
        ),
        "total_failures": failures,
    }
    payload = {
        "benchmark_id": str(benchmark.get("benchmark_id") or "benchmark"),
        "correctness_ok": failures == 0
        and all(bool(item.get("converged")) for item in payload_cases),
        "summary_metrics": summary_metrics,
        "cases": payload_cases,
    }
    if args.emit_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
