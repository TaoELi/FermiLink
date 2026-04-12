from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


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


def _script_path() -> Path:
    return Path(__file__).resolve().parents[1] / "bin" / "fermilink-optimize-cpp"


def _init_cpp_repo(repo_dir: Path, branch: str = "develop") -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "CMakeLists.txt").write_text(
        (
            "cmake_minimum_required(VERSION 3.20)\n"
            "project(mockcpp LANGUAGES CXX)\n"
            "add_library(mockcpp STATIC src/mock.cpp)\n"
        ),
        encoding="utf-8",
    )
    (repo_dir / "src").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "mock.cpp").write_text(
        "int mock_cpp_symbol() { return 1; }\n",
        encoding="utf-8",
    )
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


def _write_goal_file(goal_path: Path, package: str = "lammps") -> None:
    goal_path.write_text(
        (
            "# Optimization Goal\n"
            "\n"
            "## Package\n"
            f"{package}\n"
            "\n"
            "## Target\n"
            "Improve runtime for representative native-code workloads.\n"
            "\n"
            "## Editable Scope\n"
            "- src/**\n"
            "\n"
            "## Performance Metric\n"
            "Wall-clock time.\n"
            "\n"
            "## Representative Workloads\n"
            "- Example workload\n"
            "\n"
            "## Language\n"
            "cpp\n"
        ),
        encoding="utf-8",
    )


def test_optimize_cpp_launcher_defaults_to_repo_root_and_sibling_worktree(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "lammps"
    _init_cpp_repo(repo_dir, branch="develop")

    goal_path = tmp_path / "cpp-lammps-comm-goal.md"
    _write_goal_file(goal_path, package="lammps")

    completed = subprocess.run(
        [
            "bash",
            str(_script_path()),
            "--goal",
            str(goal_path),
            "--fermilink-bin",
            "true",
            "--dry-run",
            "--",
            "--max-iterations",
            "12",
        ],
        cwd=str(repo_dir),
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Prepared goal-mode optimize run (cpp workflow):" in completed.stdout
    assert f"  project_root:   {repo_dir}" in completed.stdout
    assert f"  worktree:       {tmp_path / 'lammps-comm'}" in completed.stdout
    assert "  branch:         fermilink-optimize/lammps-comm" in completed.stdout
    assert "  base_ref:       develop" in completed.stdout
    assert "--skills-source" not in completed.stdout
    true_bin = shutil.which("true")
    assert true_bin
    assert f"  fermilink_bin:  {true_bin}" in completed.stdout
    assert (
        f"  {true_bin} optimize {goal_path} --goal --branch "
        "fermilink-optimize/lammps-comm --max-iterations 12 "
    ) in completed.stdout

    worktree_dir = tmp_path / "lammps-comm"
    assert worktree_dir.is_dir()

    status = _git(worktree_dir, "status", "--porcelain")
    assert (status.stdout or "").strip() == ""

    head_subject = _git(worktree_dir, "show", "-s", "--format=%s", "HEAD")
    assert (head_subject.stdout or "").strip() == "initial"

    exclude_raw = _git(worktree_dir, "rev-parse", "--git-path", "info/exclude")
    exclude_path = Path((exclude_raw.stdout or "").strip())
    if not exclude_path.is_absolute():
        exclude_path = (worktree_dir / exclude_path).resolve()
    exclude_lines = exclude_path.read_text(encoding="utf-8").splitlines()
    assert ".fermilink-optimize/" in exclude_lines
    assert ".fermilink-home/" in exclude_lines


def test_optimize_cpp_launcher_help_lists_goal_and_worktree_flags() -> None:
    completed = subprocess.run(
        ["bash", str(_script_path()), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--goal, --goal-file PATH" in completed.stdout
    assert "--worktree-root PATH" in completed.stdout
    assert "current working git repo is used" in completed.stdout


def test_optimize_cpp_launcher_rejects_missing_goal_file(tmp_path: Path) -> None:
    repo_dir = tmp_path / "lammps"
    _init_cpp_repo(repo_dir)

    completed = subprocess.run(
        [
            "bash",
            str(_script_path()),
            "--goal",
            str(tmp_path / "missing-goal.md"),
            "--fermilink-bin",
            "true",
            "--dry-run",
        ],
        cwd=str(repo_dir),
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "Goal file does not exist" in completed.stderr
