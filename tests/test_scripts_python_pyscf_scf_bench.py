from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load_bench_module():
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "python-pyscf-scf-bench.py"
    )
    spec = importlib.util.spec_from_file_location("python_pyscf_scf_bench", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolve_case_threads_uses_profile_value() -> None:
    module = _load_bench_module()
    benchmark = {
        "thread_profiles": {
            "single_cpu": {"threads": 1},
            "smp_node": {"threads": 24},
        }
    }
    case = {
        "execution_profile": "smp",
        "thread_profile": "smp_node",
    }
    threads, profile = module._resolve_case_threads(case, benchmark)
    assert threads == 24
    assert profile == "smp_node"


def test_resolve_case_threads_uses_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_bench_module()
    monkeypatch.setenv("FERMILINK_PYSCF_SMP_THREADS", "12")
    case = {"execution_profile": "smp"}
    threads, profile = module._resolve_case_threads(case, {})
    assert threads == 12
    assert profile == "smp_node"


def test_performance_guardrails_detect_regressions() -> None:
    module = _load_bench_module()
    benchmark = {
        "performance_guardrails": {
            "enabled": True,
            "max_relative_regression": {
                "single_weighted_median_wall_seconds": 0.02,
            },
            "max_relative_speedup_drop": {
                "paired_geomean_smp_speedup": 0.05,
            },
        }
    }
    summary_metrics = {
        "single_weighted_median_wall_seconds": 10.4,
        "paired_geomean_smp_speedup": 1.70,
    }
    incumbent_summary = {
        "single_weighted_median_wall_seconds": 10.0,
        "paired_geomean_smp_speedup": 2.0,
    }
    errors = module._evaluate_performance_guardrails(
        benchmark=benchmark,
        summary_metrics=summary_metrics,
        incumbent_summary=incumbent_summary,
    )
    assert len(errors) == 2
    assert "single_weighted_median_wall_seconds" in errors[0]
    assert "paired_geomean_smp_speedup" in errors[1]


def test_geometric_mean_behaviour() -> None:
    module = _load_bench_module()
    assert module._geometric_mean([2.0, 8.0]) == pytest.approx(4.0)
    assert module._geometric_mean([0.0, 8.0]) == float("inf")


def test_guardrail_regression_keeps_correctness_true_when_cases_converge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_bench_module()
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "incumbent_metrics": {
                    "summary_metrics": {
                        "weighted_median_wall_seconds": 10.0,
                    }
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    benchmark = {
        "benchmark_id": "mock",
        "cases": [
            {
                "id": "case-1",
                "weight": 1.0,
                "execution_profile": "single",
                "pair_key": "pair-1",
                "scf": {"method": "RHF"},
            }
        ],
        "performance_guardrails": {
            "enabled": True,
            "state_json_path": str(state_path),
            "max_relative_regression": {
                "weighted_median_wall_seconds": 0.01,
            },
        },
    }

    original = module._run_case_subprocess

    def fake_run_case_subprocess(_case, *, threads):
        return {
            "id": "case-1",
            "converged": True,
            "wall_seconds": 12.0,
            "scf_iterations": 7,
            "total_energy_hartree": -1.0,
            "density_matrix": [1.0],
            "mo_energies": [0.1],
            "peak_rss_mb": 42.0,
            "method": "RHF",
            "method_family": "hf",
            "execution_profile": "single",
            "thread_profile": "single_cpu",
            "threads": int(threads),
            "pair_key": "pair-1",
            "error": "",
        }

    module._run_case_subprocess = fake_run_case_subprocess
    try:
        payload = module._run_benchmark(benchmark)
    finally:
        module._run_case_subprocess = original

    assert payload["correctness_ok"] is True
    assert payload.get("guardrail_errors")
    summary = payload["summary_metrics"]
    assert summary["performance_guardrail_failures"] == pytest.approx(1.0)


def test_incumbent_normalized_wall_ratio_metrics(tmp_path: Path) -> None:
    module = _load_bench_module()
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "incumbent_metrics": {
                    "cases": [
                        {"id": "case-a", "converged": True, "wall_seconds": 10.0},
                        {"id": "case-b", "converged": True, "wall_seconds": 20.0},
                    ]
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    benchmark = {
        "benchmark_id": "mock-ratio",
        "cases": [
            {
                "id": "case-a",
                "weight": 1.0,
                "execution_profile": "single",
                "pair_key": "pair-a",
                "scf": {"method": "RHF"},
            },
            {
                "id": "case-b",
                "weight": 1.0,
                "execution_profile": "single",
                "pair_key": "pair-b",
                "scf": {"method": "RHF"},
            },
        ],
        "performance_guardrails": {
            "enabled": False,
            "state_json_path": str(state_path),
        },
    }

    def fake_run_case_subprocess(case, *, threads):
        case_id = str(case.get("id") or "")
        if case_id == "case-a":
            wall = 5.0
        elif case_id == "case-b":
            wall = 40.0
        else:
            wall = 10.0
        return {
            "id": case_id,
            "converged": True,
            "wall_seconds": wall,
            "scf_iterations": 7,
            "total_energy_hartree": -1.0,
            "density_matrix": [1.0],
            "mo_energies": [0.1],
            "s2": 0.0,
            "peak_rss_mb": 42.0,
            "method": "RHF",
            "method_family": "hf",
            "execution_profile": "single",
            "thread_profile": "single_cpu",
            "threads": int(threads),
            "pair_key": f"pair-{case_id}",
            "error": "",
        }

    original = module._run_case_subprocess
    module._run_case_subprocess = fake_run_case_subprocess
    try:
        payload = module._run_benchmark(benchmark)
    finally:
        module._run_case_subprocess = original

    summary = payload["summary_metrics"]
    assert summary["wall_ratio_case_count_vs_incumbent"] == pytest.approx(2.0)
    assert summary["mean_wall_ratio_vs_incumbent"] == pytest.approx(1.25)
    assert summary["geomean_wall_ratio_vs_incumbent"] == pytest.approx(1.0)
    cases = payload["cases"]
    assert isinstance(cases, list) and len(cases) == 2
    ratio_by_case = {str(item["id"]): float(item["wall_ratio_vs_incumbent"]) for item in cases}
    assert ratio_by_case["case-a"] == pytest.approx(0.5)
    assert ratio_by_case["case-b"] == pytest.approx(2.0)
