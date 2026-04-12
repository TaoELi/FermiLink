#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import functools
import json
import resource
import statistics
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import yaml
from pyscf import dft, gto, lib

HARTREE_TO_EV = 27.2114


def _peak_rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return float(usage) / (1024.0 * 1024.0)
    return float(usage) / 1024.0


def _to_float_list(values: Any) -> list[float]:
    array = np.real_if_close(np.asarray(values))
    if np.iscomplexobj(array):
        raise ValueError("expected real-valued array")
    if array.ndim == 0:
        return [float(array.item())]
    return [float(item) for item in array.reshape(-1).tolist()]


def _weighted_median(entries: list[tuple[float, float]]) -> float:
    if not entries:
        return 0.0
    cleaned = [(float(value), max(0.0, float(weight))) for value, weight in entries]
    total_weight = sum(weight for _, weight in cleaned)
    if total_weight <= 0.0:
        cleaned = [(value, 1.0) for value, _ in cleaned]
        total_weight = float(len(cleaned))
    midpoint = 0.5 * total_weight
    cumulative = 0.0
    for value, weight in sorted(cleaned, key=lambda item: item[0]):
        cumulative += weight
        if cumulative >= midpoint:
            return value
    return cleaned[-1][0]


def _bool_case(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _float_case(value: Any, default: float) -> float:
    if isinstance(value, bool) or value is None:
        return default
    return float(value)


def _int_case(value: Any, default: int) -> int:
    if isinstance(value, bool) or value is None:
        return default
    return int(value)


def _build_molecule(case: dict[str, Any]) -> gto.Mole:
    mol = gto.Mole()
    mol.verbose = 0
    mol.output = "/dev/null"
    mol.atom = str(case.get("atom") or "").strip()
    mol.basis = str(case.get("basis") or "631g").strip()
    mol.charge = _int_case(case.get("charge"), 0)
    mol.spin = _int_case(case.get("spin"), 0)
    if case.get("unit"):
        mol.unit = str(case.get("unit"))
    mol.build()
    return mol


def _build_mean_field(case: dict[str, Any]):
    family = str(case.get("scf_family") or "").strip().lower()
    if family not in {"rks", "uks"}:
        raise ValueError(f"unsupported scf_family: {family}")
    mol = _build_molecule(case)
    xc = str(case.get("xc") or "").strip()
    scf_conv_tol = _float_case(case.get("scf_conv_tol"), 1.0e-10)

    if family == "rks":
        mf = dft.RKS(mol)
    else:
        mf = dft.UKS(mol)
    mf.xc = xc
    mf.conv_tol = scf_conv_tol
    if _bool_case(case.get("grids_prune_none"), default=True):
        mf.grids.prune = None
    if _bool_case(case.get("scf_newton"), default=False):
        mf = mf.newton()
    return mf


def _make_td_object(mf, case: dict[str, Any]):
    method_name = str(case.get("td_method") or "").strip()
    if not method_name:
        raise ValueError("case is missing td_method")
    td_factory = getattr(mf, method_name, None)
    if td_factory is None or not callable(td_factory):
        raise ValueError(f"unsupported td_method: {method_name}")
    td = td_factory()
    if hasattr(td, "singlet") and "singlet" in case:
        td.singlet = bool(case.get("singlet"))
    if "td_conv_tol" in case:
        td.conv_tol = _float_case(case.get("td_conv_tol"), td.conv_tol)
    if "td_lindep" in case:
        td.lindep = _float_case(case.get("td_lindep"), td.lindep)
    if "td_max_cycle" in case:
        td.max_cycle = _int_case(case.get("td_max_cycle"), td.max_cycle)
    if "positive_eig_threshold" in case:
        td.positive_eig_threshold = _float_case(
            case.get("positive_eig_threshold"), td.positive_eig_threshold
        )
    if hasattr(td, "deg_eia_thresh") and "deg_eia_thresh" in case:
        td.deg_eia_thresh = _float_case(case.get("deg_eia_thresh"), td.deg_eia_thresh)
    return td


def _converged_ok(td: Any, nstates: int, nroots_found: int) -> bool:
    raw = getattr(td, "converged", None)
    if isinstance(raw, np.ndarray):
        flags = [bool(item) for item in raw.reshape(-1).tolist()]
    elif isinstance(raw, (list, tuple)):
        flags = [bool(item) for item in raw]
    elif raw is None:
        flags = []
    else:
        return bool(raw) and nroots_found >= nstates
    if not flags:
        return nroots_found >= nstates
    return all(flags[:nstates]) and nroots_found >= nstates


@contextlib.contextmanager
def _solver_counter_patch() -> Iterator[dict[str, int]]:
    from pyscf.tdscf import _lr_eig as lr_eig_mod
    from pyscf.tdscf import rhf as rhf_mod
    from pyscf.tdscf import rks as rks_mod
    from pyscf.tdscf import uhf as uhf_mod
    from pyscf.tdscf import uks as uks_mod

    counters = {
        "davidson_iterations": 0,
        "matvec_batches": 0,
        "matvec_vectors": 0,
        "eigh_solver_calls": 0,
        "eig_solver_calls": 0,
        "real_eig_solver_calls": 0,
        "eigh_iterations": 0,
        "eig_iterations": 0,
        "real_eig_iterations": 0,
    }
    originals: list[tuple[Any, str, Any]] = []

    def make_wrapper(label: str, original):
        @functools.wraps(original)
        def wrapped(aop, *args, **kwargs):
            call_key = f"{label}_solver_calls"
            iter_key = f"{label}_iterations"
            counters[call_key] += 1

            @functools.wraps(aop)
            def counted_aop(vectors):
                array = np.asarray(vectors)
                batch_size = 1 if array.ndim <= 1 else int(array.shape[0])
                counters["davidson_iterations"] += 1
                counters["matvec_batches"] += 1
                counters["matvec_vectors"] += batch_size
                counters[iter_key] += 1
                return aop(vectors)

            return original(counted_aop, *args, **kwargs)

        return wrapped

    patch_specs = [
        (lr_eig_mod, "eigh", "eigh"),
        (lr_eig_mod, "eig", "eig"),
        (lr_eig_mod, "real_eig", "real_eig"),
        (rhf_mod, "lr_eigh", "eigh"),
        (rhf_mod, "lr_eig", "eig"),
        (rhf_mod, "real_eig", "real_eig"),
        (rks_mod, "lr_eigh", "eigh"),
        (uhf_mod, "lr_eigh", "eigh"),
        (uhf_mod, "lr_eig", "eig"),
        (uhf_mod, "real_eig", "real_eig"),
        (uks_mod, "lr_eigh", "eigh"),
    ]

    try:
        for module, attr_name, label in patch_specs:
            original = getattr(module, attr_name)
            originals.append((module, attr_name, original))
            setattr(module, attr_name, make_wrapper(label, original))
        yield counters
    finally:
        for module, attr_name, original in reversed(originals):
            setattr(module, attr_name, original)


def _solver_path(counters: dict[str, int]) -> str:
    if counters["real_eig_solver_calls"] > 0:
        return "real_eig"
    if counters["eig_solver_calls"] > 0:
        return "eig"
    if counters["eigh_solver_calls"] > 0:
        return "eigh"
    return "unknown"


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    case_id = str(case.get("id") or "case").strip() or "case"
    nstates = _int_case(case.get("nstates"), 1)
    atom_specific_treutler = case.get("atom_specific_treutler_grids")
    env_ctx = (
        lib.temporary_env(
            dft.radi,
            ATOM_SPECIFIC_TREUTLER_GRIDS=bool(atom_specific_treutler),
        )
        if atom_specific_treutler is not None
        else contextlib.nullcontext()
    )

    started = time.perf_counter()
    counters = {
        "davidson_iterations": 0,
        "matvec_batches": 0,
        "matvec_vectors": 0,
        "eigh_solver_calls": 0,
        "eig_solver_calls": 0,
        "real_eig_solver_calls": 0,
        "eigh_iterations": 0,
        "eig_iterations": 0,
        "real_eig_iterations": 0,
    }
    try:
        with env_ctx:
            mf = _build_mean_field(case)
            scf_started = time.perf_counter()
            mf.kernel()
            scf_seconds = time.perf_counter() - scf_started

            with _solver_counter_patch() as live_counters:
                counters = live_counters
                td_started = time.perf_counter()
                td = _make_td_object(mf, case)
                energies, _ = td.kernel(nstates=nstates)
                wall_seconds = time.perf_counter() - td_started

            post_started = time.perf_counter()
            oscillator_strengths = td.oscillator_strength(gauge="length")
            postprocess_seconds = time.perf_counter() - post_started

        energy_list = _to_float_list(energies)
        oscillator_list = _to_float_list(oscillator_strengths)
        nroots_found = len(energy_list)
        converged = _converged_ok(td, nstates=nstates, nroots_found=nroots_found)
        total_seconds = time.perf_counter() - started
        return {
            "id": case_id,
            "converged": converged,
            "wall_seconds": wall_seconds,
            "td_kernel_seconds": wall_seconds,
            "scf_seconds": scf_seconds,
            "postprocess_seconds": postprocess_seconds,
            "total_seconds": total_seconds,
            "nroots_requested": nstates,
            "nroots_found": nroots_found,
            "excitation_energies_hartree": energy_list,
            "excitation_energies_ev": [value * HARTREE_TO_EV for value in energy_list],
            "oscillator_strengths_length": oscillator_list,
            "davidson_iterations": counters["davidson_iterations"],
            "matvec_batches": counters["matvec_batches"],
            "matvec_vectors": counters["matvec_vectors"],
            "eigh_solver_calls": counters["eigh_solver_calls"],
            "eig_solver_calls": counters["eig_solver_calls"],
            "real_eig_solver_calls": counters["real_eig_solver_calls"],
            "solver_path": _solver_path(counters),
            "error": "",
        }
    except Exception as exc:  # pragma: no cover - failure path is benchmark output
        total_seconds = time.perf_counter() - started
        return {
            "id": case_id,
            "converged": False,
            "wall_seconds": total_seconds,
            "td_kernel_seconds": total_seconds,
            "scf_seconds": 0.0,
            "postprocess_seconds": 0.0,
            "total_seconds": total_seconds,
            "nroots_requested": nstates,
            "nroots_found": 0,
            "excitation_energies_hartree": [],
            "excitation_energies_ev": [],
            "oscillator_strengths_length": [],
            "davidson_iterations": counters["davidson_iterations"],
            "matvec_batches": counters["matvec_batches"],
            "matvec_vectors": counters["matvec_vectors"],
            "eigh_solver_calls": counters["eigh_solver_calls"],
            "eig_solver_calls": counters["eig_solver_calls"],
            "real_eig_solver_calls": counters["real_eig_solver_calls"],
            "solver_path": _solver_path(counters),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(limit=5),
        }


def _summary_metrics(case_results: list[dict[str, Any]], benchmark_id: str) -> dict[str, Any]:
    weighted_wall_entries: list[tuple[float, float]] = []
    weighted_scf_entries: list[tuple[float, float]] = []
    weighted_total_entries: list[tuple[float, float]] = []
    iteration_values: list[float] = []
    matvec_values: list[float] = []

    for case in case_results:
        weight = _float_case(case.get("weight"), 1.0)
        weighted_wall_entries.append((float(case.get("wall_seconds") or 0.0), weight))
        weighted_scf_entries.append((float(case.get("scf_seconds") or 0.0), weight))
        weighted_total_entries.append((float(case.get("total_seconds") or 0.0), weight))
        iteration_values.append(float(case.get("davidson_iterations") or 0.0))
        matvec_values.append(float(case.get("matvec_vectors") or 0.0))

    total_failures = sum(1 for case in case_results if not bool(case.get("converged")))
    return {
        "benchmark_id": benchmark_id,
        "correctness_ok": total_failures == 0,
        "summary_metrics": {
            "weighted_median_wall_seconds": _weighted_median(weighted_wall_entries),
            "weighted_median_td_kernel_seconds": _weighted_median(weighted_wall_entries),
            "weighted_median_scf_seconds": _weighted_median(weighted_scf_entries),
            "weighted_median_total_seconds": _weighted_median(weighted_total_entries),
            "mean_davidson_iterations": (
                statistics.fmean(iteration_values) if iteration_values else 0.0
            ),
            "mean_matvec_vectors": (
                statistics.fmean(matvec_values) if matvec_values else 0.0
            ),
            "peak_rss_mb": _peak_rss_mb(),
            "total_failures": float(total_failures),
        },
        "cases": case_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--emit-json", action="store_true")
    args = parser.parse_args()

    benchmark_path = Path(args.benchmark).resolve()
    benchmark_payload = yaml.safe_load(benchmark_path.read_text(encoding="utf-8"))
    if not isinstance(benchmark_payload, dict):
        raise SystemExit("invalid benchmark payload")

    benchmark_id = str(benchmark_payload.get("benchmark_id") or "pyscf-tddft-davidson")
    raw_cases = benchmark_payload.get("cases")
    cases = [item for item in raw_cases if isinstance(item, dict)] if isinstance(raw_cases, list) else []

    case_results = []
    for case in cases:
        result = _run_case(case)
        result["weight"] = _float_case(case.get("weight"), 1.0)
        case_results.append(result)

    payload = _summary_metrics(case_results, benchmark_id)
    guardrail_errors = [
        f"{case['id']}: {case['error']}"
        for case in case_results
        if isinstance(case.get("error"), str) and str(case.get("error")).strip()
    ]
    if guardrail_errors:
        payload["guardrail_errors"] = guardrail_errors

    if args.emit_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
