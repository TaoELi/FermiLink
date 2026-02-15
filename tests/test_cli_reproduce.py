from __future__ import annotations

import json
from pathlib import Path

import pytest

from fermilink import cli
from fermilink.cli.commands import workflows as workflow_commands


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
        '<reproduce_plan>{"tasks":[{"id":"task_001","prompt_markdown":"do x"}]}</reproduce_plan>\n'
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
        lambda _args: (_ for _ in ()).throw(
            AssertionError("loop should not run in --plan-only")
        ),
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

    code = cli.main(["reproduce", "paper.md", "--task-max-runs", "3"])
    assert code == 0
    assert len(loop_calls) == 3
    assert loop_calls[0].name == "task_001.md"
    assert loop_calls[1].name == "task_001.md"
    assert loop_calls[2].name == "task_002.md"
    assert "Before acting, read `projects/memory.md`." in loop_preambles[0]

    runs_root = repo_dir / "projects" / "reproduce"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    assert f"projects/reproduce/{latest_run}/plan.json" in loop_preambles[0]
    assert (
        f"projects/reproduce/{latest_run}/archive/memory_task_001_run_02.md"
        in loop_preambles[2]
    )
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["current_task_index"] == 2
    assert (run_dir / "archive" / "memory_task_001_run_02.md").is_file()
    assert (run_dir / "archive" / "memory_task_002_run_01.md").is_file()

    memory = (repo_dir / "projects" / "memory.md").read_text(encoding="utf-8")
    assert "## Workflow context" in memory
    assert f"projects/reproduce/{latest_run}/plan.json" in memory
    assert f"projects/reproduce/{latest_run}/state.json" in memory
    assert (
        f"projects/reproduce/{latest_run}/archive/memory_task_001_run_02.md" in memory
    )


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
        lambda _args: (_ for _ in ()).throw(
            AssertionError("loop should not run in --report-only")
        ),
    )
    code = cli.main(["reproduce", "paper.md", "--report-only"])
    assert code == 0


def test_reproduce_report_only_requires_existing_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "paper.md").write_text("paper request", encoding="utf-8")
    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)

    code = cli.main(["reproduce", "paper.md", "--report-only"])
    assert code == 2
    assert "requires an existing resumable reproduce run" in capsys.readouterr().err


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


def test_reproduce_does_not_retry_provider_failure_exit_code_one(
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

    loop_calls = {"count": 0}

    def fake_loop(loop_args) -> int:
        loop_calls["count"] += 1
        loop_args._fermilink_loop_outcome = {
            "status": "provider_failure",
            "reason": "provider_exit_code_1",
            "provider_exit_code": 1,
        }
        return 1

    monkeypatch.setattr(cli, "_cmd_loop", fake_loop)
    monkeypatch.setattr(
        cli,
        "_finalize_workflow_report",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("report should not run when task fails")
        ),
    )

    code = cli.main(["reproduce", "paper.md", "--task-max-runs", "3"])
    assert code == 1
    assert loop_calls["count"] == 1

    runs_root = repo_dir / "projects" / "reproduce"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert "provider exit code 1" in str(state["last_error"])
    run_log = json.loads(
        (run_dir / "logs" / "task_001_run_01.json").read_text(encoding="utf-8")
    )
    assert run_log["loop_status"] == "provider_failure"
    assert run_log["provider_exit_code"] == 1


def test_finalize_workflow_report_uses_run_scoped_report_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    run_dir = repo_dir / "projects" / "reproduce" / "run-001"
    runs_root = run_dir.parent
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "plan.json").write_text('{"tasks":[{"id":"task_001"}]}\n', encoding="utf-8")

    run_id = run_dir.name
    generation_marker = f"<!-- FERMILINK_REPORT_STAGE:generated run_id={run_id} -->"
    audit_marker = f"<!-- FERMILINK_REPORT_STAGE:audited run_id={run_id} -->"

    def fake_exec_turn(**kwargs) -> dict[str, object]:
        prompt = str(kwargs.get("prompt") or "")
        summary_path = run_dir / "summaries" / "task_001" / "summary.md"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        report_path = run_dir / "report.md"
        if "workflow report generation mode" in prompt:
            summary_path.write_text("# Task 1 summary\n", encoding="utf-8")
            report_path.write_text(
                f"# Report\n{generation_marker}\n",
                encoding="utf-8",
            )
        elif "workflow report audit mode" in prompt:
            report_path.write_text(
                f"# Report (audited)\n{generation_marker}\n{audit_marker}\n",
                encoding="utf-8",
            )
        return {"return_code": 0, "assistant_text": "", "stderr": ""}

    monkeypatch.setattr(workflow_commands, "_run_reproduce_exec_turn", fake_exec_turn)

    info = cli._finalize_workflow_report(
        repo_dir=repo_dir,
        run_dir=run_dir,
        runs_root=runs_root,
        workflow_name="reproduce",
        source_description="paper.md",
        tasks_state=[{"id": "task_001", "title": "Task one"}],
        requested_package_id=None,
        sandbox_override=None,
        codex_bin="codex",
    )
    assert Path(str(info["report_path"])) == run_dir / "report.md"
    assert not (runs_root / "report.md").exists()


def test_finalize_workflow_report_rejects_stale_generation_outputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    run_dir = repo_dir / "projects" / "reproduce" / "run-002"
    runs_root = run_dir.parent
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "plan.json").write_text('{"tasks":[{"id":"task_001"}]}\n', encoding="utf-8")
    summary_path = run_dir / "summaries" / "task_001" / "summary.md"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text("# Existing summary\n", encoding="utf-8")
    generation_marker = (
        f"<!-- FERMILINK_REPORT_STAGE:generated run_id={run_dir.name} -->"
    )
    (run_dir / "report.md").write_text(
        f"# Existing report\n{generation_marker}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        workflow_commands,
        "_run_reproduce_exec_turn",
        lambda **_kwargs: {"return_code": 0, "assistant_text": "", "stderr": ""},
    )

    with pytest.raises(cli.PackageError, match="validation failed"):
        cli._finalize_workflow_report(
            repo_dir=repo_dir,
            run_dir=run_dir,
            runs_root=runs_root,
            workflow_name="reproduce",
            source_description="paper.md",
            tasks_state=[{"id": "task_001", "title": "Task one"}],
            requested_package_id=None,
            sandbox_override=None,
            codex_bin="codex",
        )


def test_finalize_workflow_report_rejects_stale_audit_outputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    run_dir = repo_dir / "projects" / "reproduce" / "run-003"
    runs_root = run_dir.parent
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "plan.json").write_text('{"tasks":[{"id":"task_001"}]}\n', encoding="utf-8")

    run_id = run_dir.name
    generation_marker = f"<!-- FERMILINK_REPORT_STAGE:generated run_id={run_id} -->"
    call_counter = {"count": 0}

    def fake_exec_turn(**kwargs) -> dict[str, object]:
        call_counter["count"] += 1
        prompt = str(kwargs.get("prompt") or "")
        summary_path = run_dir / "summaries" / "task_001" / "summary.md"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        report_path = run_dir / "report.md"
        if "workflow report generation mode" in prompt:
            summary_path.write_text("# Task 1 summary\n", encoding="utf-8")
            report_path.write_text(
                f"# Report\n{generation_marker}\n",
                encoding="utf-8",
            )
        return {"return_code": 0, "assistant_text": "", "stderr": ""}

    monkeypatch.setattr(workflow_commands, "_run_reproduce_exec_turn", fake_exec_turn)

    with pytest.raises(cli.PackageError, match="report audit"):
        cli._finalize_workflow_report(
            repo_dir=repo_dir,
            run_dir=run_dir,
            runs_root=runs_root,
            workflow_name="reproduce",
            source_description="paper.md",
            tasks_state=[{"id": "task_001", "title": "Task one"}],
            requested_package_id=None,
            sandbox_override=None,
            codex_bin="codex",
        )
    assert call_counter["count"] == 3
