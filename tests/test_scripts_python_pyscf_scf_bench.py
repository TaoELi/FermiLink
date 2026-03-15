from __future__ import annotations

import importlib.util
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
