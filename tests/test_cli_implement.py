from __future__ import annotations

import subprocess
from pathlib import Path

from fermilink import cli
from fermilink.cli.commands import implement as implement_command


SAMPLE_GOAL = """\
# Implementation Goal

## Package
mockpkg

## Target
Add a new alternative SCF routine.

## Editable Scope
- feature.py

## Input API
Expose `run_new_scf(mol, *, max_cycle=50)`.

## Validation
```
python validate_impl.py
```

## Done Criteria
- API is importable
"""


def _git(repo_dir: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(repo_dir),
        text=True,
        capture_output=True,
        check=True,
    )
    return (completed.stdout or "").strip()


def _init_git_repo(repo_dir: Path) -> None:
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


def test_implement_parser_supports_core_flags() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(
        [
            "implement",
            "goal.md",
            "--project-root",
            "repo",
            "--plan-only",
            "--resume",
            "--baseline-only",
            "--allow-dirty",
            "--branch",
            "fermilink-implement/custom",
            "--max-iterations",
            "3",
            "--stop-on-consecutive-rejections",
            "2",
            "--timeout-seconds",
            "40",
            "--worker-max-iterations",
            "4",
            "--worker-provider",
            "codex",
            "--worker-model",
            "gpt-test",
            "--worker-wait-seconds",
            "0.5",
            "--worker-max-wait-seconds",
            "10",
            "--worker-pid-stall-seconds",
            "20",
            "--forever",
            "--sandbox",
            "workspace-write",
        ]
    )

    assert args.command == "implement"
    assert args.goal == "goal.md"
    assert args.project_root == "repo"
    assert args.plan_only is True
    assert args.resume is True
    assert args.baseline_only is True
    assert args.allow_dirty is True
    assert args.branch == "fermilink-implement/custom"
    assert args.max_iterations == 3
    assert args.stop_on_consecutive_rejections == 2
    assert args.timeout_seconds == 40
    assert args.worker_max_iterations == 4
    assert args.worker_provider == "codex"
    assert args.worker_model == "gpt-test"
    assert args.worker_wait_seconds == 0.5
    assert args.worker_max_wait_seconds == 10
    assert args.worker_pid_stall_seconds == 20
    assert args.forever is True
    assert args.sandbox == "workspace-write"
    assert args.func is cli._cmd_implement


def test_implement_parser_supports_status_mode() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(
        ["implement", "status", "--project-root", "repo", "--tail", "12", "--json"]
    )

    assert args.command == "implement"
    assert args.goal == "status"
    assert args.project_root == "repo"
    assert args.tail == 12
    assert args.json is True


def test_implement_run_alias_delegates_to_goal_campaign(monkeypatch) -> None:
    calls: list[str] = []

    class FakeController:
        @staticmethod
        def run_goal_campaign(args):
            calls.append(args.goal)
            return {
                "status": "planned",
                "package_id": "mockpkg",
                "branch": "fermilink-implement/mockpkg",
                "contract_path": "contract.yaml",
                "plan_path": "plan.md",
                "results_path": "results.tsv",
            }

    class FakeState:
        @staticmethod
        def run_lock_path(project_root: Path) -> Path:
            return project_root / ".fermilink-implement" / "run.lock.json"

        @staticmethod
        def clear_run_lock(_path: Path) -> None:
            return None

    monkeypatch.setattr(
        implement_command,
        "_implement_controller",
        lambda: FakeController,
    )
    monkeypatch.setattr(implement_command, "_implement_state", lambda: FakeState)

    code = cli.main(["implement", "run", "goal.md", "--project-root", "."])

    assert code == 0
    assert calls == ["goal.md"]


def test_implement_plan_only_initializes_via_cli_and_clears_run_lock(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "goal.md").write_text(SAMPLE_GOAL, encoding="utf-8")
    (repo / "feature.py").write_text("VALUE = 'base'\n", encoding="utf-8")
    (repo / "validate_impl.py").write_text("print('ok')\n", encoding="utf-8")
    _init_git_repo(repo)

    code = cli.main(
        [
            "implement",
            "goal.md",
            "--project-root",
            str(repo),
            "--plan-only",
        ]
    )

    assert code == 0
    implement_root = repo / ".fermilink-implement"
    assert (implement_root / "state.json").is_file()
    assert (implement_root / "autogen" / "goal.md").is_file()
    assert (implement_root / "autogen" / "implementation_contract.yaml").is_file()
    assert (implement_root / "autogen" / "implementation_plan.md").is_file()
    assert not (implement_root / "run.lock.json").exists()
