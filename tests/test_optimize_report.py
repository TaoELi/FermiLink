from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_REPORT_PATH = REPO_ROOT / "skills" / "optimize-report" / "assets" / "build_report.py"


def load_build_report_module():
    module_name = "skills_optimize_report_build_report_test"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, BUILD_REPORT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def write_results(path: Path) -> None:
    path.write_text(
        "\t".join(
            [
                "iteration",
                "commit",
                "status",
                "primary_metric_name",
                "primary_metric_value",
                "description",
            ]
        )
        + "\n"
        + "\t".join(["0", "baseline0000", "baseline", "wall_seconds", "10.0", "baseline"])
        + "\n"
        + "\t".join(
            [
                "1",
                "accepted1111",
                "accepted",
                "wall_seconds",
                "8.0",
                "Reduce overhead [Improved runtime cleanly.]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def write_benchmark_yaml(path: Path, package_id: str) -> None:
    path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                f"package_id: {package_id}",
                "runtime:",
                "  pre_commands:",
                "    - - bash",
                "      - -lc",
                "      - echo build",
                "cases:",
                "  - id: train-small",
                "    weight: 1.0",
                "    run_steps: 100",
                "  - id: test-large",
                "    weight: 0.5",
                "    run_steps: 200",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def init_git_repo(repo_root: Path, branch: str = "main") -> None:
    subprocess.run(["git", "init", "-b", branch, str(repo_root)], check=True, capture_output=True, text=True)


def test_build_report_renders_goal_mode_rerun_section(tmp_path: Path) -> None:
    build_report = load_build_report_module()

    repo_root = tmp_path / "demo"
    repo_root.mkdir()
    init_git_repo(repo_root, branch="main")
    subprocess.run(
        ["git", "-C", str(repo_root), "remote", "add", "origin", "git@github.com:skilled-scipkg/demo.git"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"],
        check=True,
        capture_output=True,
        text=True,
    )

    optimize_dir = repo_root / ".fermilink-optimize"
    autogen = optimize_dir / "autogen"
    autogen.mkdir(parents=True)
    write_results(optimize_dir / "results.tsv")
    (autogen / "goal.md").write_text(
        (
            "# Optimization Goal\n\n"
            "## Package\ndemo\n\n"
            "## Language\npython\n\n"
            "## Build\n"
            "```bash\n"
            "python -m pip install -e .\n"
            "pytest -q tests/test_smoke.py\n"
            "```\n"
        ),
        encoding="utf-8",
    )
    write_benchmark_yaml(autogen / "benchmark.yaml", "demo")
    (autogen / "benchmark_runner.py").write_text("print('runner')\n", encoding="utf-8")
    (autogen / "goal_inputs.json").write_text(json.dumps({"files": []}), encoding="utf-8")
    (autogen / "goal_mode.json").write_text(
        json.dumps({"package_id": "demo", "language": "python"}),
        encoding="utf-8",
    )

    out_dir = tmp_path / "report"
    build_report.build(optimize_dir, out_dir)

    index_text = (out_dir / "index.rst").read_text(encoding="utf-8")

    assert ":download:`goal.md <contract/goal.md>`" in index_text
    assert "git clone git@github.com:skilled-scipkg/demo.git" in index_text
    assert "https://github.com/skilled-scipkg/demo/tree/main" in index_text
    assert "fermilink-optimize-python" in index_text
    assert (
        "git worktree add -b fermilink-optimize/demo-<modified-feature> "
        "../demo-<modified-feature> main"
    ) in index_text
    assert "Path 1: rerun from the bundled :download:`goal.md <contract/goal.md>`." in index_text
    assert "Path 2: rerun more deterministically" in index_text
    assert "fermilink optimize demo \"$PWD\"" in index_text
    assert "Building environment" in index_text
    assert "These commands come from the copied ``## Build`` block" in index_text
    assert "python -m pip install -e ." in index_text
    assert "pytest -q tests/test_smoke.py" in index_text
    assert "Benchmarks" in index_text
    assert "Worker iterations run the ``train-*`` benchmark cases below" in index_text
    assert "Controller reviews run the ``test-*`` benchmark cases below" in index_text
    assert "id: train-small" in index_text
    assert "id: test-large" in index_text
    assert (out_dir / "contract" / "goal.md").exists()


def test_build_report_renders_fallback_rerun_section_without_goal(tmp_path: Path) -> None:
    build_report = load_build_report_module()

    repo_root = tmp_path / "fallback-demo"
    repo_root.mkdir()
    optimize_dir = repo_root / ".fermilink-optimize"
    autogen = optimize_dir / "autogen"
    autogen.mkdir(parents=True)
    write_results(optimize_dir / "results.tsv")
    write_benchmark_yaml(autogen / "benchmark.yaml", "fallback-demo")
    (autogen / "benchmark_runner.py").write_text("print('runner')\n", encoding="utf-8")

    out_dir = tmp_path / "fallback-report"
    build_report.build(optimize_dir, out_dir)

    index_text = (out_dir / "index.rst").read_text(encoding="utf-8")

    assert "git clone git@github.com:skilled-scipkg/fallback-demo.git" in index_text
    assert "`upstream GitHub repo <https://github.com/skilled-scipkg/fallback-demo>`_" in index_text
    assert "../fallback-demo-<modified-feature> <default-branch>" in index_text
    assert "Path 1: rerun from the bundled :download:`goal.md <contract/goal.md>`." not in index_text
    assert "Path 2: rerun more deterministically" in index_text
    assert "launcher selection: use ``fermilink-optimize-python`` for Python repos" in index_text
    assert "Building environment" not in index_text
    assert "Benchmarks" in index_text
    assert "id: train-small" in index_text
    assert "id: test-large" in index_text
