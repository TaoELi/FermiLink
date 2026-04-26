#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")


@dataclass
class CaseResult:
    case_id: str
    status: str
    score: float
    elapsed_seconds: float
    notes: str = ""
    reference_roots_hartree: list[float] | None = None
    peak_positions_hartree: list[float] | None = None
    observables: dict[str, Any] | None = None


def _json_default(value: object) -> object:
    try:
        import numpy as np

        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
    except Exception:
        pass
    if isinstance(value, complex):
        return {"real": value.real, "imag": value.imag}
    return str(value)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to read the benchmark YAML") from exc
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Benchmark YAML must contain an object: {path}")
    return payload


def _import_symbol(dotted: str) -> object | None:
    module_name, _, attr = dotted.rpartition(".")
    if not module_name or not attr:
        return None
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return None
    return getattr(module, attr, None)


def _resolve_lanczos_constructor(config: dict[str, Any]) -> tuple[object | None, str]:
    api = config.get("api") if isinstance(config.get("api"), dict) else {}
    candidates = api.get("constructor_candidates")
    if not isinstance(candidates, list):
        candidates = []
    for raw in candidates:
        name = str(raw or "").strip()
        if not name:
            continue
        symbol = _import_symbol(name)
        if symbol is not None:
            return symbol, name
    return None, ""


def _frequency_grid(config: dict[str, Any]):
    import numpy as np

    api = config.get("api") if isinstance(config.get("api"), dict) else {}
    grid = api.get("frequency_grid") if isinstance(api.get("frequency_grid"), dict) else {}
    start = float(grid.get("start_hartree", 0.0))
    stop = float(grid.get("stop_hartree", 1.0))
    points = int(grid.get("points", 201))
    return np.linspace(start, stop, points)


def _selected_cases(config: dict[str, Any], *, full: bool) -> list[dict[str, Any]]:
    runtime = config.get("runtime") if isinstance(config.get("runtime"), dict) else {}
    ids_key = "full_case_ids" if full else "default_case_ids"
    requested = runtime.get(ids_key)
    requested_ids = {str(item) for item in requested} if isinstance(requested, list) else set()
    cases = config.get("cases")
    if not isinstance(cases, list):
        return []
    selected: list[dict[str, Any]] = []
    for item in cases:
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("id") or "")
        if not requested_ids or case_id in requested_ids:
            selected.append(item)
    return selected


def _configure_dft_determinism() -> None:
    with contextlib.suppress(Exception):
        from pyscf import dft

        dft.radi.ATOM_SPECIFIC_TREUTLER_GRIDS = False


def _build_mean_field(case: dict[str, Any]):
    from pyscf import dft, gto, scf

    mol_spec = case.get("mol") if isinstance(case.get("mol"), dict) else {}
    mf_spec = case.get("mean_field") if isinstance(case.get("mean_field"), dict) else {}
    mol = gto.M(
        atom=str(mol_spec.get("atom") or ""),
        basis=str(mol_spec.get("basis") or "sto-3g"),
        charge=int(mol_spec.get("charge", 0)),
        spin=int(mol_spec.get("spin", 0)),
        verbose=0,
    )
    kind = str(mf_spec.get("kind") or "RHF").upper()
    if kind == "RKS":
        mf = dft.RKS(mol)
        mf.xc = str(mf_spec.get("xc") or "b3lyp")
        mf.grids.prune = None
    elif kind == "RHF":
        mf = scf.RHF(mol)
    else:
        raise RuntimeError(f"Unsupported mean-field kind for this benchmark: {kind}")
    mf.conv_tol = 1.0e-10
    mf.max_cycle = 80
    energy = mf.kernel()
    if not bool(getattr(mf, "converged", False)):
        raise RuntimeError(f"SCF did not converge for {case.get('id')}")
    return mf, float(energy)


def _reference_roots(mf: object, case: dict[str, Any]) -> list[float]:
    from pyscf import tdscf

    solver_name = str(case.get("reference_solver") or "TDDFT").upper()
    nstates = int(case.get("nstates_reference") or 4)
    if solver_name == "TDHF":
        td = tdscf.TDHF(mf)
    elif solver_name == "TDA":
        td = tdscf.TDA(mf)
    else:
        td = tdscf.TDDFT(mf)
    td.nstates = nstates
    td.conv_tol = 1.0e-7
    roots = td.kernel()[0]
    return [float(x) for x in list(roots)]


def _run_lanczos(constructor: object, mf: object, config: dict[str, Any]):
    import numpy as np

    api = config.get("api") if isinstance(config.get("api"), dict) else {}
    freq = _frequency_grid(config)
    td = constructor(mf)
    for attr, raw in (
        ("nsteps", api.get("nsteps", 220)),
        ("max_cycle", api.get("nsteps", 220)),
        ("damping", api.get("damping", 0.01)),
        ("verbose", 0),
    ):
        with contextlib.suppress(Exception):
            setattr(td, attr, raw)
    polarizations = str(api.get("polarizations") or "xyz")
    errors: list[Exception] = []
    for kwargs in (
        {"freq": freq, "polarizations": polarizations},
        {"freq": freq},
        {"omega": freq, "polarizations": polarizations},
        {"omega": freq},
        {},
    ):
        try:
            result = td.kernel(**kwargs)
            break
        except TypeError as exc:
            errors.append(exc)
    else:
        raise RuntimeError(f"Lanczos kernel signature not compatible: {errors[-1]}")
    obj = result if result is not None else td
    freq_out = _first_attr(obj, ("freq", "omega", "frequencies", "frequency_grid"))
    if freq_out is None:
        freq_out = freq
    polarizability = _first_attr(obj, ("polarizability", "alpha", "response"))
    strength = _first_attr(obj, ("strength", "oscillator_strength", "spectrum", "absorption"))
    peak_positions = _first_attr(obj, ("peak_positions", "peak_energies", "e", "excitation_energies"))
    diagnostics = _first_attr(obj, ("diagnostics", "metadata", "info"))
    freq_arr = np.asarray(freq_out, dtype=float)
    strength_arr = None if strength is None else np.asarray(strength, dtype=float).reshape(-1)
    alpha_arr = None if polarizability is None else np.asarray(polarizability)
    if strength_arr is None and alpha_arr is not None:
        flattened = np.asarray(alpha_arr).reshape((len(freq_arr), -1))
        strength_arr = np.maximum(0.0, np.imag(flattened).sum(axis=1))
    if strength_arr is not None and len(strength_arr) != len(freq_arr):
        strength_arr = np.resize(strength_arr, len(freq_arr))
    if peak_positions is not None:
        peaks = [float(x) for x in np.asarray(peak_positions).reshape(-1)]
    elif strength_arr is not None:
        peaks = _peaks_from_spectrum(freq_arr, strength_arr)
    else:
        peaks = []
    if len(freq_arr) == 0:
        raise RuntimeError("Lanczos result did not provide a frequency grid")
    if alpha_arr is None and strength_arr is None:
        raise RuntimeError("Lanczos result did not provide polarizability or strength")
    if not np.all(np.isfinite(freq_arr)):
        raise RuntimeError("Lanczos frequency grid contains non-finite values")
    if strength_arr is not None and not np.all(np.isfinite(strength_arr)):
        raise RuntimeError("Lanczos strength contains non-finite values")
    return {
        "freq": freq_arr,
        "polarizability": alpha_arr,
        "strength": strength_arr,
        "peak_positions": peaks,
        "diagnostics": diagnostics if isinstance(diagnostics, dict) else {},
    }


def _first_attr(obj: object, names: tuple[str, ...]) -> object | None:
    if isinstance(obj, dict):
        for name in names:
            if name in obj:
                return obj[name]
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def _peaks_from_spectrum(freq, strength) -> list[float]:
    import numpy as np

    y = np.asarray(strength, dtype=float)
    x = np.asarray(freq, dtype=float)
    if len(y) < 3 or np.nanmax(y) <= 0:
        return []
    peaks: list[tuple[float, float]] = []
    for idx in range(1, len(y) - 1):
        if y[idx] >= y[idx - 1] and y[idx] >= y[idx + 1] and y[idx] > 0:
            peaks.append((float(y[idx]), float(x[idx])))
    peaks.sort(reverse=True)
    return [pos for _height, pos in peaks[:8]]


def _peak_alignment_score(
    reference: list[float],
    peaks: list[float],
    *,
    tolerance: float,
) -> tuple[float, str]:
    if not reference:
        return 0.0, "reference roots unavailable"
    if not peaks:
        return 0.0, "no Lanczos spectral peaks detected"
    considered = reference[: min(3, len(reference))]
    hits = 0
    deltas: list[float] = []
    for ref in considered:
        delta = min(abs(float(ref) - float(peak)) for peak in peaks)
        deltas.append(delta)
        if delta <= tolerance:
            hits += 1
    score = float(hits) / float(len(considered))
    return score, "nearest peak deltas: " + ", ".join(f"{x:.6f}" for x in deltas)


def _determinism_ok(first: dict[str, Any], second: dict[str, Any], config: dict[str, Any]) -> bool:
    import numpy as np

    scoring = config.get("scoring") if isinstance(config.get("scoring"), dict) else {}
    rtol = float(scoring.get("determinism_rtol", 1.0e-9))
    atol = float(scoring.get("determinism_atol", 1.0e-10))
    for key in ("freq", "strength", "polarizability"):
        a = first.get(key)
        b = second.get(key)
        if a is None or b is None:
            continue
        try:
            if not np.allclose(np.asarray(a), np.asarray(b), rtol=rtol, atol=atol):
                return False
        except Exception:
            return False
    return True


def _static_no_full_matrix_guard(repo_root: Path) -> tuple[bool, str]:
    path = repo_root / "pyscf" / "tdscf" / "lanczos.py"
    if not path.is_file():
        return False, "pyscf/tdscf/lanczos.py is missing"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, str(exc)
    suspicious = (".get_ab(" in text or "get_ab()" in text) and (
        "np.block" in text or "numpy.block" in text or "hstack" in text or "vstack" in text
    )
    if suspicious:
        return False, "source appears to build explicit TDDFT matrix blocks"
    return True, "no obvious full-matrix construction pattern detected"


def _run_case(
    case: dict[str, Any],
    *,
    constructor: object,
    config: dict[str, Any],
    repeat: bool,
) -> CaseResult:
    started = time.perf_counter()
    case_id = str(case.get("id") or "case")
    try:
        mf, scf_energy = _build_mean_field(case)
        roots = _reference_roots(mf, case)
        lanczos = _run_lanczos(constructor, mf, config)
        second = _run_lanczos(constructor, mf, config) if repeat else lanczos
        scoring = config.get("scoring") if isinstance(config.get("scoring"), dict) else {}
        tolerance = float(scoring.get("peak_tolerance_hartree", 0.08))
        alignment, align_notes = _peak_alignment_score(
            roots,
            lanczos.get("peak_positions") or [],
            tolerance=tolerance,
        )
        deterministic = _determinism_ok(lanczos, second, config) if repeat else True
        score = 0.65 + 0.25 * alignment + (0.10 if deterministic else 0.0)
        status = "pass" if alignment > 0.0 and deterministic else "partial"
        observables = {
            "scf_energy_hartree": scf_energy,
            "n_reference_roots": len(roots),
            "n_lanczos_peaks": len(lanczos.get("peak_positions") or []),
            "deterministic_repeat": deterministic,
            "alignment_fraction": alignment,
        }
        return CaseResult(
            case_id=case_id,
            status=status,
            score=score,
            elapsed_seconds=max(0.0, time.perf_counter() - started),
            notes=align_notes,
            reference_roots_hartree=roots,
            peak_positions_hartree=list(lanczos.get("peak_positions") or []),
            observables=observables,
        )
    except Exception as exc:
        return CaseResult(
            case_id=case_id,
            status="fail",
            score=0.0,
            elapsed_seconds=max(0.0, time.perf_counter() - started),
            notes=f"{type(exc).__name__}: {exc}",
        )


def _milestone(mid: str, status: str, score: float, notes: str = "") -> dict[str, Any]:
    return {"id": mid, "status": status, "score": float(score), "notes": notes}


def run_benchmark(config: dict[str, Any], *, full: bool, repo_root: Path) -> dict[str, Any]:
    milestones: list[dict[str, Any]] = []
    errors: list[str] = []
    observables: dict[str, Any] = {}
    try:
        _configure_dft_determinism()
        import pyscf  # noqa: F401

        milestones.append(_milestone("pyscf_imports", "pass", _weight(config, "pyscf_imports")))
    except Exception as exc:
        errors.append(f"PySCF import failed: {exc}")
        milestones.append(_milestone("pyscf_imports", "fail", 0.0, str(exc)))
        return _payload(config, milestones, [], observables, errors)

    constructor, constructor_name = _resolve_lanczos_constructor(config)
    if constructor is None:
        errors.append("LanczosTDDFT constructor not found")
        milestones.append(
            _milestone(
                "lanczos_api_available",
                "fail",
                0.0,
                "expected pyscf.tdscf.LanczosTDDFT or pyscf.tdscf.lanczos.LanczosTDDFT",
            )
        )
        guard_ok, guard_note = _static_no_full_matrix_guard(repo_root)
        milestones.append(
            _milestone(
                "static_no_full_matrix_guard",
                "pass" if guard_ok else "fail",
                _weight(config, "static_no_full_matrix_guard") if guard_ok else 0.0,
                guard_note,
            )
        )
        return _payload(config, milestones, [], observables, errors)
    milestones.append(
        _milestone(
            "lanczos_api_available",
            "pass",
            _weight(config, "lanczos_api_available"),
            constructor_name,
        )
    )
    guard_ok, guard_note = _static_no_full_matrix_guard(repo_root)
    milestones.append(
        _milestone(
            "static_no_full_matrix_guard",
            "pass" if guard_ok else "fail",
            _weight(config, "static_no_full_matrix_guard") if guard_ok else 0.0,
            guard_note,
        )
    )

    cases = _selected_cases(config, full=full)
    case_results: list[CaseResult] = []
    for index, case in enumerate(cases):
        case_results.append(
            _run_case(
                case,
                constructor=constructor,
                config=config,
                repeat=index == 0,
            )
        )
    passed_cases = [item for item in case_results if item.status in {"pass", "partial"}]
    fully_aligned_cases = [item for item in case_results if item.status == "pass"]
    case_fraction = len(passed_cases) / len(cases) if cases else 0.0
    alignment_fraction = len(fully_aligned_cases) / len(cases) if cases else 0.0
    deterministic = bool(case_results and (case_results[0].observables or {}).get("deterministic_repeat"))
    milestones.append(
        _milestone(
            "cases_run",
            "pass" if case_fraction == 1.0 else "partial" if case_fraction else "fail",
            _weight(config, "cases_run") * case_fraction,
            f"{len(passed_cases)}/{len(cases)} cases produced spectra",
        )
    )
    milestones.append(
        _milestone(
            "peak_alignment",
            "pass" if alignment_fraction == 1.0 else "partial" if alignment_fraction else "fail",
            _weight(config, "peak_alignment") * alignment_fraction,
            f"{len(fully_aligned_cases)}/{len(cases)} cases aligned with reference roots",
        )
    )
    milestones.append(
        _milestone(
            "deterministic_repeat",
            "pass" if deterministic else "fail",
            _weight(config, "deterministic_repeat") if deterministic else 0.0,
            "first selected case repeated deterministically",
        )
    )
    observables["constructor"] = constructor_name
    observables["selected_case_ids"] = [str(case.get("id") or "") for case in cases]
    return _payload(config, milestones, case_results, observables, errors)


def _weight(config: dict[str, Any], key: str) -> float:
    scoring = config.get("scoring") if isinstance(config.get("scoring"), dict) else {}
    milestones = scoring.get("milestones") if isinstance(scoring.get("milestones"), dict) else {}
    return float(milestones.get(key, 0.0))


def _payload(
    config: dict[str, Any],
    milestones: list[dict[str, Any]],
    cases: list[CaseResult],
    observables: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    score = float(sum(float(item.get("score") or 0.0) for item in milestones))
    complete_score = float((config.get("scoring") or {}).get("complete_score", 100.0))
    complete = score >= complete_score - 1.0e-9 and not errors
    commands_ok = True
    cases_payload = [
        {
            "id": item.case_id,
            "status": item.status,
            "score_fraction": item.score,
            "elapsed_seconds": item.elapsed_seconds,
            "notes": item.notes,
            "reference_roots_hartree": item.reference_roots_hartree or [],
            "peak_positions_hartree": item.peak_positions_hartree or [],
            "observables": item.observables or {},
        }
        for item in cases
    ]
    return {
        "ok": complete,
        "status": "complete" if complete else "partial",
        "score": score,
        "complete": complete,
        "build_ok": any(item["id"] == "pyscf_imports" and item["status"] == "pass" for item in milestones),
        "api_ok": any(item["id"] == "lanczos_api_available" and item["status"] == "pass" for item in milestones),
        "scientific_checks_ok": complete,
        "commands_ok": commands_ok,
        "milestones": milestones,
        "cases": cases_payload,
        "observables": observables,
        "errors": errors,
        "hard_reject": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--benchmark",
        default="scripts/implement/python_pyscf/python-pyscf-lanczos-tddft-benchmark.yaml",
    )
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    benchmark_path = Path(args.benchmark).expanduser()
    config = _load_yaml(benchmark_path)
    runtime = config.get("runtime") if isinstance(config.get("runtime"), dict) else {}
    full_env = str(runtime.get("full_env_flag") or "FERMILINK_LANCZOS_TDDFT_FULL")
    full = bool(args.full or os.getenv(full_env, "").strip() in {"1", "true", "yes", "on"})
    payload = run_benchmark(config, full=full, repo_root=Path.cwd())
    text = json.dumps(payload, sort_keys=True, default=_json_default)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
