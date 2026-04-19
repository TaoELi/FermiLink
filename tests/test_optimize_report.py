from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("MPLCONFIGDIR", "/tmp/fermilink-matplotlib")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp/fermilink-cache")

pytest.importorskip("matplotlib")


ROOT = Path(__file__).resolve().parents[1]
BUILD_REPORT_PATH = ROOT / "skills" / "optimize-report" / "assets" / "build_report.py"
PLOT_OPTIMIZE_PATH = ROOT / "skills" / "optimize-report" / "assets" / "plot_optimize.py"


def _git(
    repo_dir: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo_dir),
        text=True,
        capture_output=True,
        check=check,
    )


def _init_git_repo_with_remote(repo_dir: Path, branch: str) -> Path:
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _git(repo_dir, "init", "-b", branch)
    _git(repo_dir, "add", ".")
    _git(
        repo_dir,
        "-c",
        "user.name=Tests",
        "-c",
        "user.email=tests@example.com",
        "commit",
        "-m",
        "initial",
    )
    remote_dir = repo_dir.parent / f"{repo_dir.name}-remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote_dir)],
        text=True,
        capture_output=True,
        check=True,
    )
    _git(repo_dir, "remote", "add", "origin", str(remote_dir))
    return remote_dir


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


def test_build_report_copies_benchmark_inputs_and_indexes_them(tmp_path: Path) -> None:
    build_report = _load_build_report_module()

    optimize_dir = tmp_path / ".fermilink-optimize"
    autogen_dir = optimize_dir / "autogen"
    inputs_dir = optimize_dir / "inputs" / "all"
    (inputs_dir / "sub").mkdir(parents=True, exist_ok=True)
    autogen_dir.mkdir(parents=True, exist_ok=True)

    (optimize_dir / "results.tsv").write_text(
        (
            "iteration\tcommit\tstatus\tprimary_metric_name\tprimary_metric_value\tdescription\n"
            "0\tbaseline123456\tbaseline\twall_seconds\t10.0\tbaseline\n"
        ),
        encoding="utf-8",
    )
    (autogen_dir / "benchmark.yaml").write_text(
        (
            "package_id: mockpkg\n"
            "cases:\n"
            "  - id: train-a\n"
            "    command: [\"python\", \"benchmark_runner.py\"]\n"
        ),
        encoding="utf-8",
    )
    (autogen_dir / "benchmark_runner.py").write_text(
        "print('benchmark')\n",
        encoding="utf-8",
    )
    (autogen_dir / "goal.md").write_text("# Goal\n", encoding="utf-8")
    (autogen_dir / "goal_inputs.json").write_text("{}", encoding="utf-8")
    (autogen_dir / "setup_env.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (inputs_dir / "shared.dat").write_text("shared\n", encoding="utf-8")
    (inputs_dir / "sub" / "case.in").write_text("case\n", encoding="utf-8")

    out_dir = tmp_path / "optimize-report"
    build_report.build(
        optimize_dir=optimize_dir,
        out_dir=out_dir,
        title="Optimization Report Test",
        metric_label="Wall time",
        direction="lower",
    )

    assert (out_dir / "inputs" / "all" / "shared.dat").read_text(encoding="utf-8") == "shared\n"
    assert (out_dir / "inputs" / "all" / "sub" / "case.in").read_text(encoding="utf-8") == "case\n"

    index_text = (out_dir / "index.rst").read_text(encoding="utf-8")
    assert "Optimization Trajectory" in index_text
    assert "Rerun Guide" in index_text
    assert "Benchmark Examples" in index_text
    assert "Input files for Benchmarks" in index_text
    assert index_text.index("Benchmark Contracts") < index_text.index("Input files for Benchmarks")
    assert index_text.index("Input files for Benchmarks") < index_text.index("Runtime Data")
    contract_section = index_text.split("Benchmark Contracts\n-------------------\n", 1)[1].split(
        "Input files for Benchmarks\n--------------------------\n", 1
    )[0]
    assert ":download:`benchmark.yaml <contract/benchmark.yaml>`" in contract_section
    assert ":download:`benchmark_runner.py <contract/benchmark_runner.py>`" in contract_section
    assert ":download:`goal.md <contract/goal.md>`" in contract_section
    assert ":download:`goal_inputs.json <contract/goal_inputs.json>`" not in contract_section
    assert ":download:`setup_env.sh <contract/setup_env.sh>`" not in contract_section
    assert ":download:`shared.dat <inputs/all/shared.dat>`" in index_text
    assert ":download:`sub/case.in <inputs/all/sub/case.in>`" in index_text
    assert "mkdir -p .fermilink-optimize/autogen .fermilink-optimize/inputs/all" in index_text
    assert "cp -R /path/to/report/inputs/all/. .fermilink-optimize/inputs/all/" in index_text


def test_github_repo_url_from_remote_handles_common_github_formats() -> None:
    build_report = _load_build_report_module()

    assert (
        build_report.github_repo_url_from_remote("git@github.com:skilled-scipkg/mockpkg.git")
        == "https://github.com/skilled-scipkg/mockpkg"
    )
    assert (
        build_report.github_repo_url_from_remote("ssh://git@github.com/skilled-scipkg/mockpkg.git")
        == "https://github.com/skilled-scipkg/mockpkg"
    )
    assert (
        build_report.github_repo_url_from_remote("https://github.com/skilled-scipkg/mockpkg.git")
        == "https://github.com/skilled-scipkg/mockpkg"
    )
    assert build_report.github_repo_url_from_remote("/tmp/local-remote.git") is None


def test_build_report_adds_github_commit_links_when_branch_is_published(tmp_path: Path) -> None:
    build_report = _load_build_report_module()

    optimize_dir = tmp_path / ".fermilink-optimize"
    runs_dir = optimize_dir / "runs" / "iter_0001"
    runs_dir.mkdir(parents=True, exist_ok=True)

    (optimize_dir / "results.tsv").write_text(
        (
            "iteration\tcommit\tstatus\tprimary_metric_name\tprimary_metric_value\tdescription\n"
            "0\t111111111111aaaa\tbaseline\twall_seconds\t10.0\tbaseline\n"
            "1\t222222222222bbbb\taccepted\twall_seconds\t8.0\tvectorized inner loop [accepted]\n"
        ),
        encoding="utf-8",
    )

    published_branch = build_report.PublishedBranch(
        branch="fermilink-optimize/mockpkg-feature",
        remote="origin",
        remote_url="git@github.com:skilled-scipkg/mockpkg.git",
        repo_url="https://github.com/skilled-scipkg/mockpkg",
        branch_url="https://github.com/skilled-scipkg/mockpkg/tree/fermilink-optimize%2Fmockpkg-feature",
    )

    out_dir = tmp_path / "optimize-report"
    build_report.build(
        optimize_dir=optimize_dir,
        out_dir=out_dir,
        title="Optimization Report Test",
        metric_label="Wall time",
        direction="lower",
        published_branch=published_branch,
    )

    index_text = (out_dir / "index.rst").read_text(encoding="utf-8")
    assert "published GitHub branch" in index_text
    assert "https://github.com/skilled-scipkg/mockpkg/commit/111111111111" in index_text
    assert "https://github.com/skilled-scipkg/mockpkg/commit/222222222222" in index_text
    assert "`111111111111 <summary-baseline-111111111111_>`_" in index_text
    assert "`222222222222 <summary-best-222222222222_>`_" in index_text

    iter_text = (out_dir / "iterations" / "iter_0001_accepted.rst").read_text(encoding="utf-8")
    assert "GitHub commit:" in iter_text
    assert "Published branch:" in iter_text
    assert "https://github.com/skilled-scipkg/mockpkg/commit/222222222222" in iter_text


def test_push_optimize_branch_creates_remote_branch_with_set_upstream(tmp_path: Path) -> None:
    build_report = _load_build_report_module()

    repo_dir = tmp_path / "mockpkg"
    remote_dir = _init_git_repo_with_remote(
        repo_dir,
        branch="fermilink-optimize/mockpkg-feature",
    )
    optimize_dir = repo_dir / ".fermilink-optimize"
    optimize_dir.mkdir(parents=True, exist_ok=True)

    published_branch = build_report.push_optimize_branch(optimize_dir)

    assert published_branch.branch == "fermilink-optimize/mockpkg-feature"
    assert published_branch.remote == "origin"
    assert published_branch.repo_url is None
    pushed_ref = subprocess.run(
        [
            "git",
            "--git-dir",
            str(remote_dir),
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/fermilink-optimize/mockpkg-feature",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert pushed_ref.returncode == 0


def test_push_optimize_branch_rejects_non_optimize_branch(tmp_path: Path) -> None:
    build_report = _load_build_report_module()

    repo_dir = tmp_path / "mockpkg"
    _init_git_repo_with_remote(repo_dir, branch="main")
    optimize_dir = repo_dir / ".fermilink-optimize"
    optimize_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(SystemExit, match="does not start with 'fermilink-optimize'"):
        build_report.push_optimize_branch(optimize_dir)
