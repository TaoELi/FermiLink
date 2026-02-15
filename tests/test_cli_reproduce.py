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


def test_reproduce_parser_enforce_simulation_disables_dry_run() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["reproduce", "paper.md", "--enforce-simulation"])
    assert args.dry_run is False


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


def test_reproduce_dry_run_plan_only_forwards_flag_and_persists_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "paper.md").write_text("paper request", encoding="utf-8")

    captured: dict[str, object] = {}
    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)

    def fake_generate(**kwargs):
        captured["dry_run"] = kwargs.get("dry_run")
        return {
            "version": 1,
            "paper_source": "paper.md",
            "assumptions": [],
            "tasks": [
                {
                    "id": "task_001",
                    "title": "task one",
                    "prompt_markdown": "prepare scripts only",
                }
            ],
        }

    monkeypatch.setattr(cli, "_generate_reproduce_plan", fake_generate)
    monkeypatch.setattr(
        cli,
        "_cmd_loop",
        lambda _args: (_ for _ in ()).throw(
            AssertionError("loop should not run in --plan-only")
        ),
    )

    code = cli.main(["reproduce", "paper.md", "--plan-only", "--dry-run"])
    assert code == 0
    assert captured.get("dry_run") is True

    runs_root = repo_dir / "projects" / "reproduce"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["dry_run"] is True


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


def test_reproduce_dry_run_adds_loop_constraints(
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
    code = cli.main(["reproduce", "paper.md", "--dry-run", "--skip-report"])
    assert code == 0
    assert len(loop_preambles) == 1
    assert "DRY-RUN mode constraints:" in loop_preambles[0]
    assert "Do not execute full simulations" in loop_preambles[0]
    assert "1-4 focused steps" in loop_preambles[0]
    assert "overrides the default loop guidance of 5-15 steps" in loop_preambles[0]

    runs_root = repo_dir / "projects" / "reproduce"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["dry_run"] is True


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


def test_reproduce_resume_rejects_mismatched_dry_run_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
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

    assert cli.main(["reproduce", "paper.md", "--plan-only"]) == 0
    code = cli.main(["reproduce", "paper.md", "--enforce-simulation"])
    assert code == 2
    assert "matching dry-run mode" in capsys.readouterr().err


def test_reproduce_resume_rejects_mismatched_data_dir_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "paper.md").write_text("paper request", encoding="utf-8")
    data_dir = repo_dir / "input_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "input.txt").write_text("alpha", encoding="utf-8")

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

    assert cli.main(["reproduce", "paper.md", "--plan-only", "--data-dir", "input_data"]) == 0
    code = cli.main(["reproduce", "paper.md"])
    assert code == 2
    assert "matching data-dir mode" in capsys.readouterr().err


def test_reproduce_data_dir_read_only_detects_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "paper.md").write_text("paper request", encoding="utf-8")
    data_dir = repo_dir / "input_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    data_file = data_dir / "input.txt"
    data_file.write_text("before", encoding="utf-8")

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

    assert cli.main(["reproduce", "paper.md", "--plan-only", "--data-dir", "input_data"]) == 0

    loop_calls = {"count": 0}

    def fake_loop(_loop_args) -> int:
        loop_calls["count"] += 1
        data_file.write_text("after", encoding="utf-8")
        return 0

    monkeypatch.setattr(cli, "_cmd_loop", fake_loop)
    code = cli.main(
        ["reproduce", "paper.md", "--data-dir", "input_data", "--skip-report"]
    )
    assert code == 1
    assert loop_calls["count"] == 1

    runs_root = repo_dir / "projects" / "reproduce"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    state = json.loads((runs_root / latest_run / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert "Read-only data guard violation" in str(state["last_error"])


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


def test_generate_reproduce_plan_dry_run_appends_prompt_requirements(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    prompts: list[str] = []

    plan_payload = {
        "version": 1,
        "paper_source": "paper.md",
        "assumptions": [],
        "tasks": [
            {
                "id": "task_001",
                "title": "task one",
                "objective": "objective",
                "prompt_markdown": "prepare only",
            }
        ],
    }

    def fake_exec_turn(**kwargs) -> dict[str, object]:
        prompt = str(kwargs.get("prompt") or "")
        prompts.append(prompt)
        assistant_text = (
            "<reproduce_plan>"
            + json.dumps(plan_payload)
            + "</reproduce_plan>"
        )
        return {"return_code": 0, "assistant_text": assistant_text, "stderr": ""}

    monkeypatch.setattr(workflow_commands, "_run_reproduce_exec_turn", fake_exec_turn)

    plan = cli._generate_reproduce_plan(
        repo_dir=repo_dir,
        source_text="source",
        source_description="paper.md",
        requested_package_id=None,
        sandbox_override=None,
        codex_bin="codex",
        planner_max_tries=1,
        auditor_max_tries=1,
        dry_run=True,
    )
    assert plan["version"] == 1
    assert len(prompts) == 2
    assert "Dry-run planning requirements:" in prompts[0]
    assert "Dry-run audit requirements:" in prompts[1]


def test_generate_reproduce_plan_with_data_auditor_writes_task_data_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    run_dir = repo_dir / "projects" / "reproduce" / "run-001"
    data_root = run_dir / "data"
    data_root.mkdir(parents=True, exist_ok=True)

    source_data_dir = repo_dir / "input_data"
    (source_data_dir / "inputs").mkdir(parents=True, exist_ok=True)
    (source_data_dir / "inputs" / "base.json").write_text(
        '{"alpha": 1}\n', encoding="utf-8"
    )
    (source_data_dir / "tables").mkdir(parents=True, exist_ok=True)
    (source_data_dir / "tables" / "results.csv").write_text(
        "x,y\n1,2\n", encoding="utf-8"
    )

    manifest_payload = {
        "version": 1,
        "data_dir": str(source_data_dir),
        "scan_limits": {
            "max_files": 4000,
            "max_total_bytes": 1073741824,
            "max_file_bytes": 67108864,
            "hash_max_bytes": 1048576,
        },
        "fingerprint": "abc123",
        "files": [
            {
                "path": "inputs/base.json",
                "size": 12,
                "mtime_ns": 1,
                "type": "structured_text",
            },
            {
                "path": "tables/results.csv",
                "size": 10,
                "mtime_ns": 2,
                "type": "tabular_or_text",
            },
        ],
        "skipped": [],
        "stats": {
            "indexed_files": 2,
            "indexed_bytes": 22,
            "skipped_files": 0,
            "truncated": False,
            "truncated_reason": "",
        },
    }
    (data_root / "data_manifest.json").write_text(
        json.dumps(manifest_payload, indent=2) + "\n",
        encoding="utf-8",
    )
    (data_root / "data_summary.md").write_text(
        "# Data Summary\n- indexed_files: 2\n",
        encoding="utf-8",
    )

    planner_plan = {
        "version": 1,
        "paper_source": "paper.md",
        "assumptions": [],
        "tasks": [
            {
                "id": "task_001",
                "title": "task one",
                "objective": "objective",
                "prompt_markdown": "run task one",
            }
        ],
    }
    task_map_payload = {
        "version": 1,
        "tasks": [
            {
                "id": "task_001",
                "files": [
                    {
                        "path": "inputs/base.json",
                        "rationale": "base input",
                        "confidence": 0.9,
                    }
                ],
                "unknowns": [],
                "notes": [],
            }
        ],
        "global_unknowns": [],
    }

    prompts: list[str] = []

    def fake_exec_turn(**kwargs) -> dict[str, object]:
        prompt = str(kwargs.get("prompt") or "")
        prompts.append(prompt)
        if "workflow data auditor mode" in prompt:
            return {
                "return_code": 0,
                "assistant_text": "<task_data_map>"
                + json.dumps(task_map_payload)
                + "</task_data_map>",
                "stderr": "",
            }
        return {
            "return_code": 0,
            "assistant_text": "<reproduce_plan>"
            + json.dumps(planner_plan)
            + "</reproduce_plan>",
            "stderr": "",
        }

    monkeypatch.setattr(workflow_commands, "_run_reproduce_exec_turn", fake_exec_turn)

    data_context = {
        "enabled": True,
        "workflow": "reproduce",
        "source_path": str(source_data_dir),
        "source_path_input": "input_data",
        "read_only": True,
        "limits": {
            "max_files": 4000,
            "max_total_bytes": 1073741824,
            "max_file_bytes": 67108864,
            "hash_max_bytes": 1048576,
        },
        "artifacts": {
            "root": "projects/reproduce/run-001/data",
            "manifest": "projects/reproduce/run-001/data/data_manifest.json",
            "summary": "projects/reproduce/run-001/data/data_summary.md",
            "task_map": "projects/reproduce/run-001/data/task_data_map.json",
        },
    }

    plan = cli._generate_reproduce_plan(
        repo_dir=repo_dir,
        run_dir=run_dir,
        source_text="source",
        source_description="paper.md",
        requested_package_id=None,
        sandbox_override=None,
        codex_bin="codex",
        planner_max_tries=1,
        auditor_max_tries=1,
        dry_run=False,
        data_context=data_context,
    )
    assert plan["version"] == 1
    assert len(prompts) == 3
    assert "workflow data auditor mode" in prompts[1]
    assert (data_root / "task_data_map.json").is_file()
    task_context = data_root / "task_001.md"
    assert task_context.is_file()
    context_text = task_context.read_text(encoding="utf-8")
    assert "Only use files listed below" in context_text
    tasks = plan.get("tasks")
    assert isinstance(tasks, list)
    assert str(tasks[0]["data_context_file"]).endswith("data/task_001.md")
