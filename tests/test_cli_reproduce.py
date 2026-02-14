from __future__ import annotations

import json
from pathlib import Path

import pytest

from fermilink import cli


def test_reproduce_parser_defaults() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["reproduce", "paper.md"])
    assert args.command == "reproduce"
    assert args.task_max_runs == 5
    assert args.planner_max_tries == 2
    assert args.auditor_max_tries == 2
    assert args.max_iterations == 10
    assert args.wait_seconds == 0.0
    assert args.max_wait_seconds == 600.0
    assert args.plan_only is False
    assert args.report_only is False
    assert args.skip_report is False
    assert args.resume is True


def test_extract_reproduce_plan_payload_parses_tagged_json() -> None:
    text = (
        "some text\n"
        "<reproduce_plan>{\"tasks\":[{\"id\":\"task_001\",\"prompt_markdown\":\"do x\"}]}</reproduce_plan>\n"
        "tail"
    )
    payload = cli._extract_reproduce_plan_payload(text)
    assert isinstance(payload, dict)
    tasks = payload.get("tasks")
    assert isinstance(tasks, list)
    assert tasks[0]["id"] == "task_001"


def test_reproduce_plan_only_writes_plan_without_running_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "paper.md").write_text("paper request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_reproduce_plan",
        lambda **_kwargs: {
            "version": 1,
            "paper_source": "paper.md",
            "assumptions": [],
            "tasks": [
                {
                    "id": "task_001",
                    "title": "task one",
                    "prompt_markdown": "run task one",
                }
            ],
        },
    )
    monkeypatch.setattr(
        cli,
        "_cmd_loop",
        lambda _args: (_ for _ in ()).throw(AssertionError("loop should not run in --plan-only")),
    )

    code = cli.main(["reproduce", "paper.md", "--plan-only"])
    assert code == 0

    runs_root = repo_dir / "projects" / "reproduce"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    assert (run_dir / "plan.json").is_file()
    assert (run_dir / "prompts" / "task_001.md").is_file()

    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "plan_ready"
    assert state["current_task_index"] == 0


def test_reproduce_executes_tasks_with_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "paper.md").write_text("paper request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_reproduce_plan",
        lambda **_kwargs: {
            "version": 1,
            "paper_source": "paper.md",
            "assumptions": [],
            "tasks": [
                {
                    "id": "task_001",
                    "title": "task one",
                    "prompt_markdown": "run task one",
                },
                {
                    "id": "task_002",
                    "title": "task two",
                    "prompt_markdown": "run task two",
                },
            ],
        },
    )

    loop_calls: list[Path] = []
    run_results = [1, 0, 0]

    def fake_loop(loop_args) -> int:
        prompt_values = getattr(loop_args, "prompt", [])
        assert isinstance(prompt_values, list)
        loop_calls.append(Path(str(prompt_values[0])))
        return run_results[len(loop_calls) - 1]

    monkeypatch.setattr(cli, "_cmd_loop", fake_loop)
    monkeypatch.setattr(
        cli,
        "_finalize_workflow_report",
        lambda **kwargs: {
            "report_path": str(Path(kwargs["runs_root"]) / "report.md"),
            "summaries_root": str(Path(kwargs["run_dir"]) / "summaries"),
            "summary_count": 2,
        },
    )

    code = cli.main(["reproduce", "paper.md", "--task-max-runs", "3"])
    assert code == 0
    assert len(loop_calls) == 3
    assert loop_calls[0].name == "task_001.md"
    assert loop_calls[1].name == "task_001.md"
    assert loop_calls[2].name == "task_002.md"

    runs_root = repo_dir / "projects" / "reproduce"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["current_task_index"] == 2
    assert (run_dir / "archive" / "memory_task_001_run_02.md").is_file()
    assert (run_dir / "archive" / "memory_task_002_run_01.md").is_file()


def test_reproduce_resume_reuses_existing_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "paper.md").write_text("paper request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_reproduce_plan",
        lambda **_kwargs: {
            "version": 1,
            "paper_source": "paper.md",
            "assumptions": [],
            "tasks": [
                {
                    "id": "task_001",
                    "title": "task one",
                    "prompt_markdown": "run task one",
                }
            ],
        },
    )
    monkeypatch.setattr(cli, "_cmd_loop", lambda _args: 0)
    monkeypatch.setattr(
        cli,
        "_finalize_workflow_report",
        lambda **kwargs: {
            "report_path": str(Path(kwargs["runs_root"]) / "report.md"),
            "summaries_root": str(Path(kwargs["run_dir"]) / "summaries"),
            "summary_count": 1,
        },
    )

    assert cli.main(["reproduce", "paper.md", "--plan-only"]) == 0

    monkeypatch.setattr(
        cli,
        "_generate_reproduce_plan",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("existing plan should be reused on resume")
        ),
    )
    assert cli.main(["reproduce", "paper.md"]) == 0


def test_reproduce_skip_report_bypasses_report_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "paper.md").write_text("paper request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_reproduce_plan",
        lambda **_kwargs: {
            "version": 1,
            "paper_source": "paper.md",
            "assumptions": [],
            "tasks": [
                {
                    "id": "task_001",
                    "title": "task one",
                    "prompt_markdown": "run task one",
                }
            ],
        },
    )
    monkeypatch.setattr(cli, "_cmd_loop", lambda _args: 0)
    monkeypatch.setattr(
        cli,
        "_finalize_workflow_report",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("report should be skipped with --skip-report")
        ),
    )

    code = cli.main(["reproduce", "paper.md", "--skip-report"])
    assert code == 0

    runs_root = repo_dir / "projects" / "reproduce"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    report = state.get("report")
    assert isinstance(report, dict)
    assert report.get("skipped") is True


def test_reproduce_report_only_runs_report_stage_without_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "paper.md").write_text("paper request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_reproduce_plan",
        lambda **_kwargs: {
            "version": 1,
            "paper_source": "paper.md",
            "assumptions": [],
            "tasks": [
                {
                    "id": "task_001",
                    "title": "task one",
                    "prompt_markdown": "run task one",
                }
            ],
        },
    )
    monkeypatch.setattr(cli, "_cmd_loop", lambda _args: 0)
    monkeypatch.setattr(
        cli,
        "_finalize_workflow_report",
        lambda **kwargs: {
            "report_path": str(Path(kwargs["runs_root"]) / "report.md"),
            "summaries_root": str(Path(kwargs["run_dir"]) / "summaries"),
            "summary_count": 1,
        },
    )
    assert cli.main(["reproduce", "paper.md", "--plan-only"]) == 0

    monkeypatch.setattr(
        cli,
        "_cmd_loop",
        lambda _args: (_ for _ in ()).throw(AssertionError("loop should not run in --report-only")),
    )
    code = cli.main(["reproduce", "paper.md", "--report-only"])
    assert code == 0


def test_reproduce_plan_only_conflicts_with_report_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "paper.md").write_text("paper request", encoding="utf-8")
    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    code = cli.main(["reproduce", "paper.md", "--plan-only", "--report-only"])
    assert code == 2
