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
    return Path(__file__).resolve().parents[1] / "bin" / "fermilink-optimize-python"


def _init_python_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "pyproject.toml").write_text(
        (
            "[build-system]\n"
            'requires = ["setuptools>=61"]\n'
            'build-backend = "setuptools.build_meta"\n'
            "\n"
            "[project]\n"
            'name = "mockpkg"\n'
            'version = "0.0.1"\n'
        ),
        encoding="utf-8",
    )
    (repo_dir / "skills").mkdir(parents=True, exist_ok=True)
    (repo_dir / "skills" / "README.md").write_text("skills\n", encoding="utf-8")
    _git(repo_dir, "init", "-b", "main")
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


def _write_goal_file(goal_path: Path) -> None:
    goal_path.write_text(
        (
            "# Optimization Goal\n"
            "\n"
            "## Package\n"
            "mockpkg\n"
            "\n"
            "## Target\n"
            "Improve runtime for representative SCF workloads.\n"
            "\n"
            "## Editable Scope\n"
            "- mockpkg/**\n"
            "\n"
            "## Performance Metric\n"
            "Wall-clock time.\n"
            "\n"
            "## Representative Workloads\n"
            "- Example workload\n"
            "\n"
            "## Language\n"
            "python\n"
        ),
        encoding="utf-8",
    )


def _write_fake_python_with_stub_venv(fake_python: Path) -> None:
    fake_python.write_text(
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "\n"
            'if [[ "$#" -ge 3 && "$1" == "-m" && "$2" == "venv" ]]; then\n'
            '  venv_path="$3"\n'
            '  mkdir -p "$venv_path/bin"\n'
            "  cat >\"$venv_path/bin/python\" <<'STUBPY'\n"
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "\n"
            'if [[ "$#" -ge 4 && "$1" == "-m" && "$2" == "pip" && "$3" == "install" && "$4" == "--help" ]]; then\n'
            '  echo "  -e, --editable"\n'
            "  exit 0\n"
            "fi\n"
            'if [[ "$#" -ge 2 && "$1" == "-m" && "$2" == "pip" ]]; then\n'
            "  exit 0\n"
            "fi\n"
            'if [[ "$#" -ge 1 && "$1" == "-c" ]]; then\n'
            "  exit 0\n"
            "fi\n"
            'if [[ "$#" -ge 1 && "$1" == "-" ]]; then\n'
            "  cat >/dev/null\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n"
            "STUBPY\n"
            '  chmod +x "$venv_path/bin/python"\n'
            "  exit 0\n"
            "fi\n"
            "\n"
            'if [[ "$#" -ge 2 && "$1" == "-m" && "$2" == "pip" ]]; then\n'
            "  exit 0\n"
            "fi\n"
            'if [[ "$#" -ge 1 && "$1" == "-c" ]]; then\n'
            "  exit 0\n"
            "fi\n"
            'if [[ "$#" -ge 1 && "$1" == "-" ]]; then\n'
            "  cat >/dev/null\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n"
        ),
        encoding="utf-8",
    )
    fake_python.chmod(0o755)


def test_optimize_python_launcher_prepares_goal_mode_worktree(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    _init_python_repo(repo_dir)

    goal_path = tmp_path / "mock-goal.md"
    _write_goal_file(goal_path)

    completed = subprocess.run(
        [
            "bash",
            str(_script_path()),
            "--project-root",
            str(repo_dir),
            "--goal",
            str(goal_path),
            "--branch",
            "fermilink-optimize/mock-goal",
            "--base-ref",
            "main",
            "--skills-source",
            "existing",
            "--no-venv",
            "--fermilink-bin",
            "true",
            "--dry-run",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Prepared goal-mode optimize run (python workflow):" in completed.stdout
    assert "Committed prep files" not in completed.stdout
    true_bin = shutil.which("true")
    assert true_bin
    assert f"  fermilink_bin:  {true_bin}" in completed.stdout
    assert f"  {true_bin} optimize {goal_path} --goal --branch fermilink-optimize/mock-goal " in completed.stdout

    worktree_dir = tmp_path / ".fermilink-worktrees" / "repo" / "mock-goal"
    assert worktree_dir.is_dir()
    assert not (worktree_dir / goal_path.name).exists()

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


def test_optimize_python_launcher_help_lists_goal_and_venv_flags() -> None:
    completed = subprocess.run(
        ["bash", str(_script_path()), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--goal, --goal-file PATH" in completed.stdout
    assert "--venv-root PATH" in completed.stdout
    assert "--pip-install SPEC" in completed.stdout


def test_optimize_python_launcher_rejects_missing_goal_file(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    _init_python_repo(repo_dir)

    completed = subprocess.run(
        [
            "bash",
            str(_script_path()),
            "--project-root",
            str(repo_dir),
            "--goal",
            str(tmp_path / "missing-goal.md"),
            "--no-venv",
            "--fermilink-bin",
            "true",
            "--dry-run",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "Goal file does not exist" in completed.stderr


def test_optimize_python_launcher_defaults_venv_outside_repo(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    _init_python_repo(repo_dir)

    goal_path = tmp_path / "mock-goal.md"
    _write_goal_file(goal_path)

    fake_python = tmp_path / "fake-python"
    _write_fake_python_with_stub_venv(fake_python)

    completed = subprocess.run(
        [
            "bash",
            str(_script_path()),
            "--project-root",
            str(repo_dir),
            "--goal",
            str(goal_path),
            "--branch",
            "fermilink-optimize/mock-branch",
            "--python-bin",
            str(fake_python),
            "--skills-source",
            "existing",
            "--fermilink-bin",
            "true",
            "--dry-run",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    expected_venv = tmp_path / ".venvs" / "repo-mock-branch"
    assert expected_venv.is_dir()
    assert f"  venv:           {expected_venv}" in completed.stdout
