from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("MPLCONFIGDIR", "/tmp/fermilink-matplotlib")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp/fermilink-cache")

pytest.importorskip("matplotlib")


ROOT = Path(__file__).resolve().parents[1]
BUILD_REPORT_PATH = ROOT / "skills" / "optimize-report" / "assets" / "build_report.py"
PLOT_OPTIMIZE_PATH = ROOT / "skills" / "optimize-report" / "assets" / "plot_optimize.py"


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_build_report_module():
    return _load_module(BUILD_REPORT_PATH, "test_build_report_module")


def _load_plot_optimize_module():
    return _load_module(PLOT_OPTIMIZE_PATH, "test_plot_optimize_module")


def test_humanize_metric_label_handles_common_optimize_metrics() -> None:
    plot_optimize = _load_plot_optimize_module()

    assert (
        plot_optimize.humanize_metric_label("weighted_median_wall_seconds")
        == "Weighted median wall time (s)"
    )
    assert (
        plot_optimize.humanize_metric_label("weighted_median_wall_seconds_per_100_steps")
        == "Weighted median wall time per 100 steps (s)"
    )
    assert (
        plot_optimize.humanize_metric_label("weighted_median_pair_plus_kspace_seconds")
        == "Weighted median pair plus kspace time (s)"
    )
    assert plot_optimize.humanize_metric_label("peak_rss_mb") == "Peak RSS (MB)"
    assert (
        plot_optimize.humanize_metric_label("geomean_wall_ratio_vs_incumbent")
        == "Geometric mean wall-time ratio vs incumbent"
    )


def test_build_report_archives_goal_markdown_from_autogen(tmp_path: Path) -> None:
    build_report = _load_build_report_module()

    optimize_dir = tmp_path / ".fermilink-optimize"
    autogen_dir = optimize_dir / "autogen"
    autogen_dir.mkdir(parents=True)

    (optimize_dir / "results.tsv").write_text(
        (
            "iteration\tcommit\tstatus\tprimary_metric_name\tprimary_metric_value\tdescription\n"
            "0\tbaseline123456\tbaseline\twall_seconds\t10.0\tbaseline\n"
        ),
        encoding="utf-8",
    )
    goal_text = "# Goal\n\nArchive this file in the generated report bundle.\n"
    (autogen_dir / "goal.md").write_text(goal_text, encoding="utf-8")

    out_dir = tmp_path / "optimize-report"
    build_report.build(
        optimize_dir=optimize_dir,
        out_dir=out_dir,
        title="Optimization Report Test",
        metric_label="Wall time",
        direction="lower",
    )

    archived_goal = out_dir / "contract" / "goal.md"
    assert archived_goal.read_text(encoding="utf-8") == goal_text

    index_text = (out_dir / "index.rst").read_text(encoding="utf-8")
    assert ":download:`goal.md <contract/goal.md>`" in index_text


def test_build_report_humanizes_default_metric_label(tmp_path: Path) -> None:
    build_report = _load_build_report_module()

    optimize_dir = tmp_path / ".fermilink-optimize"
    optimize_dir.mkdir(parents=True)
    (optimize_dir / "results.tsv").write_text(
        (
            "iteration\tcommit\tstatus\tprimary_metric_name\tprimary_metric_value\tdescription\n"
            "0\tbaseline123456\tbaseline\tweighted_median_wall_seconds_per_100_steps\t10.0\tbaseline\n"
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "optimize-report"
    build_report.build(
        optimize_dir=optimize_dir,
        out_dir=out_dir,
        title="Optimization Report Test",
        direction="lower",
    )

    expected_label = "Weighted median wall time per 100 steps (s)"
    index_text = (out_dir / "index.rst").read_text(encoding="utf-8")
    assert expected_label in index_text

    summary = json.loads((out_dir / "data" / "summary.json").read_text(encoding="utf-8"))
    assert summary["metric_label"] == expected_label
