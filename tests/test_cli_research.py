from __future__ import annotations

import json
from pathlib import Path

import pytest

from fermilink import cli


def test_research_parser_defaults() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["research", "idea.md"])
    assert args.command == "research"
    assert args.task_max_runs == 5
    assert args.planner_max_tries == 2
    assert args.auditor_max_tries == 2
    assert args.max_iterations == 10
    assert args.wait_seconds == 0.0
    assert args.max_wait_seconds == 600.0
    assert args.data_dir is None
    assert args.data_writable is False
    assert args.data_max_files == 4000
    assert args.data_max_total_bytes == 1073741824
    assert args.data_max_file_bytes == 67108864
    assert args.data_hash_max_bytes == 1048576
    assert args.plan_only is False
    assert args.report_only is False
    assert args.skip_report is False
    assert args.dry_run is True
    assert args.resume is True


def test_research_parser_enforce_simulation_disables_dry_run() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["research", "idea.md", "--enforce-simulation"])
    assert args.dry_run is False


def test_extract_research_plan_payload_parses_tagged_json() -> None:
    text = (
        "some text\n"
        '<research_plan>{"tasks":[{"id":"task_001","prompt_markdown":"do x"}]}</research_plan>\n'
        "tail"
    )
    payload = cli._extract_research_plan_payload(text)
    assert isinstance(payload, dict)
    tasks = payload.get("tasks")
    assert isinstance(tasks, list)
    assert tasks[0]["id"] == "task_001"


def test_research_plan_only_writes_plan_without_running_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_research_plan",
        lambda **_kwargs: {
            "version": 1,
            "paper_source": "idea.md",
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
        lambda _args: (_ for _ in ()).throw(
            AssertionError("loop should not run in --plan-only")
        ),
    )

    code = cli.main(["research", "idea.md", "--plan-only"])
    assert code == 0

    runs_root = repo_dir / "projects" / "research"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    assert (run_dir / "plan.json").is_file()
    assert (run_dir / "prompts" / "task_001.md").is_file()

    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "plan_ready"
    assert state["current_task_index"] == 0


def test_research_dry_run_adds_loop_constraints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_research_plan",
        lambda **_kwargs: {
            "version": 1,
            "paper_source": "idea.md",
            "assumptions": [],
            "tasks": [
                {
                    "id": "task_001",
                    "title": "task one",
                    "prompt_markdown": "prepare scripts only",
                }
            ],
        },
    )

    loop_preambles: list[str] = []

    def fake_loop(loop_args) -> int:
        loop_preambles.append(str(getattr(loop_args, "workflow_prompt_preamble", "")))
        return 0

    monkeypatch.setattr(cli, "_cmd_loop", fake_loop)
    code = cli.main(["research", "idea.md", "--dry-run", "--skip-report"])
    assert code == 0
    assert len(loop_preambles) == 1
    assert "DRY-RUN mode constraints:" in loop_preambles[0]
    assert "Do not execute full simulations" in loop_preambles[0]
    assert "1-4 focused steps" in loop_preambles[0]
    assert "overrides the default loop guidance of 5-15 steps" in loop_preambles[0]

    runs_root = repo_dir / "projects" / "research"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["dry_run"] is True


def test_research_executes_tasks_with_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_research_plan",
        lambda **_kwargs: {
            "version": 1,
            "paper_source": "idea.md",
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
    loop_preambles: list[str] = []
    run_results = [1, 0, 0]

    def fake_loop(loop_args) -> int:
        prompt_values = getattr(loop_args, "prompt", [])
        assert isinstance(prompt_values, list)
        loop_calls.append(Path(str(prompt_values[0])))
        loop_preambles.append(str(getattr(loop_args, "workflow_prompt_preamble", "")))
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

    code = cli.main(["research", "idea.md", "--task-max-runs", "3"])
    assert code == 0
    assert len(loop_calls) == 3
    assert loop_calls[0].name == "task_001.md"
    assert loop_calls[1].name == "task_001.md"
    assert loop_calls[2].name == "task_002.md"
    assert "Before acting, read `projects/memory.md`." in loop_preambles[0]

    runs_root = repo_dir / "projects" / "research"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    assert f"projects/research/{latest_run}/plan.json" in loop_preambles[0]
    assert (
        f"projects/research/{latest_run}/archive/memory_task_001_run_02.md"
        in loop_preambles[2]
    )
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["current_task_index"] == 2
    assert (run_dir / "archive" / "memory_task_001_run_02.md").is_file()
    assert (run_dir / "archive" / "memory_task_002_run_01.md").is_file()

    memory = (repo_dir / "projects" / "memory.md").read_text(encoding="utf-8")
    assert "## Workflow context" in memory
    assert f"projects/research/{latest_run}/plan.json" in memory
    assert f"projects/research/{latest_run}/state.json" in memory
    assert f"projects/research/{latest_run}/archive/memory_task_001_run_02.md" in memory


def test_research_resume_uses_user_edited_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_research_plan",
        lambda **_kwargs: {
            "version": 1,
            "paper_source": "idea.md",
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
            "summary_count": 2,
        },
    )

    assert cli.main(["research", "idea.md", "--plan-only"]) == 0

    runs_root = repo_dir / "projects" / "research"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    plan_path = run_dir / "plan.json"
    edited_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    edited_plan["tasks"] = [
        {
            "id": "task_001",
            "title": "task one",
            "prompt_markdown": "run task one",
        },
        {
            "id": "task_edited",
            "title": "edited task",
            "prompt_markdown": "run edited task",
        },
    ]
    plan_path.write_text(json.dumps(edited_plan, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(
        cli,
        "_generate_research_plan",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("edited plan should run without replanning")
        ),
    )

    loop_calls: list[Path] = []
    monkeypatch.setattr(
        cli,
        "_cmd_loop",
        lambda loop_args: loop_calls.append(Path(str(loop_args.prompt[0]))) or 0,
    )

    assert cli.main(["research", "idea.md"]) == 0
    assert len(loop_calls) == 2
    assert loop_calls[0].name == "task_001.md"
    assert loop_calls[1].name == "task_edited.md"


def test_research_resume_rejects_mismatched_dry_run_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_research_plan",
        lambda **_kwargs: {
            "version": 1,
            "paper_source": "idea.md",
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
        lambda _args: (_ for _ in ()).throw(
            AssertionError("loop should not run in --plan-only")
        ),
    )

    assert cli.main(["research", "idea.md", "--plan-only"]) == 0
    code = cli.main(["research", "idea.md", "--enforce-simulation"])
    assert code == 2
    assert "matching dry-run mode" in capsys.readouterr().err


def test_research_resume_rejects_mismatched_data_dir_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")
    data_dir = repo_dir / "input_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "dataset.csv").write_text("x,y\n1,2\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_research_plan",
        lambda **_kwargs: {
            "version": 1,
            "paper_source": "idea.md",
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
        lambda _args: (_ for _ in ()).throw(
            AssertionError("loop should not run in --plan-only")
        ),
    )

    assert cli.main(["research", "idea.md", "--plan-only", "--data-dir", "input_data"]) == 0
    code = cli.main(["research", "idea.md"])
    assert code == 2
    assert "matching data-dir mode" in capsys.readouterr().err


def test_research_report_only_conflicts_with_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")
    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    code = cli.main(["research", "idea.md", "--report-only", "--restart"])
    assert code == 2


def test_research_report_only_requires_existing_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")
    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)

    code = cli.main(["research", "idea.md", "--report-only"])
    assert code == 2
    assert "requires an existing resumable research run" in capsys.readouterr().err
