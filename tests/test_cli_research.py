from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from fermilink import cli
from fermilink.cli.commands import workflows as workflow_commands


@pytest.fixture(autouse=True)
def _isolate_fermilink_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FERMILINK_HOME", str(tmp_path / ".fermilink"))


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
    assert args.pid_stall_seconds == 900.0
    assert args.hpc_profile is None
    assert not hasattr(args, "data_dir")
    assert not hasattr(args, "data_writable")
    assert not hasattr(args, "data_max_files")
    assert not hasattr(args, "data_max_total_bytes")
    assert not hasattr(args, "data_max_file_bytes")
    assert not hasattr(args, "data_hash_max_bytes")
    assert args.plan_only is False
    assert args.report_only is False
    assert args.skip_report is False
    assert not hasattr(args, "dry_run")
    assert args.resume is True


def test_research_parser_rejects_removed_dry_run_flags() -> None:
    parser = cli._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["research", "idea.md", "--dry-run"])
    with pytest.raises(SystemExit):
        parser.parse_args(["research", "idea.md", "--enforce-simulation"])


def test_research_parser_accepts_hpc_profile() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        ["research", "idea.md", "--hpc-profile", "scripts/hpc_profile_anvil.json"]
    )
    assert args.hpc_profile == "scripts/hpc_profile_anvil.json"


def test_research_parser_rejects_removed_data_dir_flags() -> None:
    parser = cli._build_parser()
    for argv in (
        ["research", "idea.md", "--data-dir", "input_data"],
        ["research", "idea.md", "--data-writable"],
        ["research", "idea.md", "--data-max-files", "10"],
        ["research", "idea.md", "--data-max-total-bytes", "1024"],
        ["research", "idea.md", "--data-max-file-bytes", "512"],
        ["research", "idea.md", "--data-hash-max-bytes", "256"],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)


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


def test_generate_research_plan_includes_unified_memory_stage_instructions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    prompts: list[str] = []
    status_updates: list[str] = []

    plan_payload = {
        "version": 1,
        "paper_source": "idea.md",
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
            "<research_plan>" + json.dumps(plan_payload) + "</research_plan>"
        )
        return {"return_code": 0, "assistant_text": assistant_text, "stderr": ""}

    monkeypatch.setattr(workflow_commands, "_run_reproduce_exec_turn", fake_exec_turn)

    plan = cli._generate_research_plan(
        repo_dir=repo_dir,
        source_text="source",
        source_description="idea.md",
        requested_package_id=None,
        sandbox_override=None,
        provider_bin_override="codex",
        planner_max_tries=1,
        auditor_max_tries=1,
        workflow_status_hook=lambda mode_text: status_updates.append(mode_text),
    )

    assert plan["version"] == 1
    assert len(prompts) == 2
    assert "Unified-memory requirements (apply in this stage):" in prompts[0]
    assert "Before acting, read `projects/memory.md`." in prompts[0]
    assert "After completing this stage, update `projects/memory.md`" in prompts[0]
    assert "### Parameter source mapping" in prompts[0]
    assert "### Simulation uncertainty" in prompts[0]
    assert "Unified-memory requirements (apply in this stage):" in prompts[1]
    assert "Before acting, read `projects/memory.md`." in prompts[1]
    assert "After completing this stage, update `projects/memory.md`" in prompts[1]
    assert "### Parameter source mapping" in prompts[1]
    assert "### Simulation uncertainty" in prompts[1]
    assert status_updates == ["research plan", "research audit"]


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
    hpc_context = state.get("hpc_context")
    assert isinstance(hpc_context, dict)
    assert hpc_context.get("enabled") is False
    assert hpc_context.get("mode") == "local"


def test_research_plan_only_uses_default_home_hpc_profile_when_flag_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")
    home = tmp_path / ".fermilink"
    monkeypatch.setenv("FERMILINK_HOME", str(home))
    profile = home / "HPC_PROFILE.json"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(
        json.dumps(
            {
                "slurm_default_partition": "debug",
                "slurm_defaults": "--nodes=1 --ntasks=2 --time=00:15:00",
                "slurm_resource_policy": "Prefer debug queue",
            }
        )
        + "\n",
        encoding="utf-8",
    )

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
    runs_root = repo_dir / "projects" / "research"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    hpc_context = state.get("hpc_context")
    assert isinstance(hpc_context, dict)
    assert hpc_context.get("enabled") is True
    assert hpc_context.get("source") == "default_home_hpc_profile"
    assert hpc_context.get("profile_path") == str(profile)


def test_research_loop_preamble_enforces_simulation_execution(
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
    code = cli.main(["research", "idea.md", "--skip-report"])
    assert code == 0
    assert len(loop_preambles) == 1
    assert "Simulation policy:" in loop_preambles[0]
    assert "Execute the simulations required by each task" in loop_preambles[0]
    assert "execution_target: local machine (workflow default)." in loop_preambles[0]

    runs_root = repo_dir / "projects" / "research"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert "dry_run" not in state


def test_research_attempts_completion_checkpoint_commit(
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

    completion_calls: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        workflow_commands,
        "_workflow_completion_commit",
        lambda *, repo_dir, mode_name: completion_calls.append(
            (Path(repo_dir), str(mode_name))
        )
        or {"status": "noop", "sha": "", "error": "", "memory_only": "false"},
    )

    code = cli.main(["research", "idea.md", "--plan-only"])
    assert code == 0
    assert completion_calls == [(repo_dir, "research")]


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
        setattr(
            loop_args,
            "_fermilink_completion_commit",
            {
                "status": "noop",
                "sha": "",
                "error": "",
                "memory_only": "false",
            },
        )
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
    assert (
        "Before acting, read short/long term memory at `projects/memory.md`."
        in loop_preambles[0]
    )
    assert (
        "Before acting, optionally read original paper or request at `idea.md` "
        "for additional context if needed."
    ) in loop_preambles[0]

    runs_root = repo_dir / "projects" / "research"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    assert f"projects/research/{latest_run}/plan.json" in loop_preambles[0]
    assert "latest archived memory" not in loop_preambles[2]
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    run_log = json.loads(
        (run_dir / "logs" / "task_001_run_01.json").read_text(encoding="utf-8")
    )
    assert run_log["completion_commit_status"] == "noop"
    assert run_log["completion_commit_sha"] == ""
    assert run_log["completion_commit_error"] == ""
    assert run_log["completion_commit_memory_only"] == "false"
    assert state["status"] == "completed"
    assert state["current_task_index"] == 2
    assert list((run_dir / "archive").glob("memory_*.md")) == []

    memory = (repo_dir / "projects" / "memory.md").read_text(encoding="utf-8")
    assert "## Workflow context" in memory
    assert f"projects/research/{latest_run}/plan.json" in memory
    assert f"projects/research/{latest_run}/state.json" in memory


def test_research_status_hook_emits_task_progress_with_totals(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")

    status_updates: list[str] = []
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

    def fake_loop(loop_args) -> int:
        iteration_hook = getattr(loop_args, "_fermilink_loop_iteration_hook", None)
        if callable(iteration_hook):
            iteration_hook(2, 10)
        return 0

    monkeypatch.setattr(cli, "_cmd_loop", fake_loop)
    parser = cli._build_parser()
    args = parser.parse_args(["research", "idea.md", "--skip-report"])
    setattr(
        args,
        "_fermilink_workflow_status_hook",
        lambda mode_text: status_updates.append(str(mode_text)),
    )

    code = cli._cmd_research(args)
    assert code == 0
    assert status_updates == [
        "research task 1/2 loop 2/10",
        "research task 2/2 loop 2/10",
    ]


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


def test_research_resume_ignores_legacy_dry_run_state(
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

    runs_root = repo_dir / "projects" / "research"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["dry_run"] = True
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

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

    code = cli.main(["research", "idea.md"])
    assert code == 0
    assert "matching dry-run mode" not in capsys.readouterr().err


def test_research_resume_ignores_legacy_data_context(
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

    runs_root = repo_dir / "projects" / "research"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["data_context"] = {
        "enabled": True,
        "source_path": str(repo_dir / "input_data"),
        "read_only": True,
        "limits": {},
        "artifacts": {},
    }
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

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

    code = cli.main(["research", "idea.md"])
    assert code == 0
    error_text = capsys.readouterr().err
    assert "matching data-dir mode" not in error_text


def test_research_resume_rejects_mismatched_hpc_profile_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")
    (repo_dir / "anvil.json").write_text(
        json.dumps(
            {
                "slurm_default_partition": "shared",
                "slurm_defaults": "--nodes=1 --ntasks=1 --ntasks-per-node=1",
                "slurm_resource_policy": "Use single-node defaults unless MPI is required",
            }
        )
        + "\n",
        encoding="utf-8",
    )

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
    code = cli.main(
        [
            "research",
            "idea.md",
            "--hpc-profile",
            "anvil.json",
        ]
    )
    assert code == 2
    assert "matching --hpc-profile mode" in capsys.readouterr().err


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


def test_research_report_only_uses_saved_hpc_context_without_mode_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")
    (repo_dir / "anvil.json").write_text(
        json.dumps(
            {
                "slurm_default_partition": "debug",
                "slurm_defaults": "--time=00:05:00",
                "slurm_resource_policy": "keep allocations small",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_research_plan",
        lambda **_kwargs: {
            "version": 1,
            "request_source": "idea.md",
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

    captured_hpc_context: dict[str, object] = {}

    def _capture_finalize(**kwargs):
        raw_hpc_context = kwargs.get("hpc_context")
        if isinstance(raw_hpc_context, dict):
            captured_hpc_context.update(raw_hpc_context)
        return {
            "report_path": str(Path(kwargs["run_dir"]) / "report.md"),
            "summaries_root": str(Path(kwargs["run_dir"]) / "summaries"),
            "summary_count": 1,
        }

    monkeypatch.setattr(cli, "_finalize_workflow_report", _capture_finalize)

    assert (
        cli.main(
            [
                "research",
                "idea.md",
                "--plan-only",
                "--hpc-profile",
                "anvil.json",
            ]
        )
        == 0
    )
    assert cli.main(["research", "idea.md", "--report-only"]) == 0
    assert captured_hpc_context.get("enabled") is True


def test_workflow_checkpoint_commit_stages_all_changes_under_limits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / ".git").mkdir()
    (repo_dir / "notes.txt").write_text("hello\n", encoding="utf-8")

    git_calls: list[tuple[str, ...]] = []

    def fake_run(cmd, **kwargs):
        assert kwargs["cwd"] == str(repo_dir)
        git_args = tuple(cmd[1:])
        git_calls.append(git_args)
        if git_args == ("rev-parse", "--is-inside-work-tree"):
            return subprocess.CompletedProcess(cmd, 0, stdout="true\n", stderr="")
        if git_args == ("status", "--porcelain"):
            return subprocess.CompletedProcess(
                cmd, 0, stdout=" M notes.txt\n", stderr=""
            )
        if git_args == ("add", "-A"):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if git_args == ("diff", "--cached", "--quiet"):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        if git_args == ("commit", "-m", "checkpoint message"):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if git_args == ("rev-parse", "--verify", "HEAD"):
            return subprocess.CompletedProcess(cmd, 0, stdout="abc123\n", stderr="")
        raise AssertionError(f"unexpected git args: {git_args}")

    monkeypatch.setattr(workflow_commands.shutil, "which", lambda name: "/usr/bin/git")
    monkeypatch.setattr(workflow_commands.subprocess, "run", fake_run)

    payload = workflow_commands._workflow_checkpoint_commit(
        repo_dir=repo_dir,
        commit_message="checkpoint message",
    )

    assert payload == {
        "status": "committed",
        "sha": "abc123",
        "error": "",
        "memory_only": "false",
    }
    assert ("add", "-A") in git_calls


def test_workflow_checkpoint_commit_falls_back_to_memory_only_when_limits_exceeded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / ".git").mkdir()
    (repo_dir / "large.bin").write_bytes(b"x" * 8)
    memory_path = (
        repo_dir
        / workflow_commands.LOOP_MEMORY_DIRNAME
        / workflow_commands.LOOP_MEMORY_FILENAME
    )
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("memory\n", encoding="utf-8")

    git_calls: list[tuple[str, ...]] = []
    expected_memory_rel = f"{workflow_commands.LOOP_MEMORY_DIRNAME}/{workflow_commands.LOOP_MEMORY_FILENAME}"

    def fake_run(cmd, **kwargs):
        assert kwargs["cwd"] == str(repo_dir)
        git_args = tuple(cmd[1:])
        git_calls.append(git_args)
        if git_args == ("rev-parse", "--is-inside-work-tree"):
            return subprocess.CompletedProcess(cmd, 0, stdout="true\n", stderr="")
        if git_args == ("status", "--porcelain"):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=f" M large.bin\n M {expected_memory_rel}\n",
                stderr="",
            )
        if git_args == ("add", "--", expected_memory_rel):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if git_args == ("diff", "--cached", "--quiet"):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        if len(git_args) == 3 and git_args[:2] == ("commit", "-m"):
            assert git_args[2].startswith("checkpoint message [memory-only:")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if git_args == ("rev-parse", "--verify", "HEAD"):
            return subprocess.CompletedProcess(cmd, 0, stdout="def456\n", stderr="")
        raise AssertionError(f"unexpected git args: {git_args}")

    monkeypatch.setattr(workflow_commands.shutil, "which", lambda name: "/usr/bin/git")
    monkeypatch.setattr(workflow_commands.subprocess, "run", fake_run)
    monkeypatch.setattr(workflow_commands, "_GIT_COMMIT_MAX_BYTES", 1)

    payload = workflow_commands._workflow_checkpoint_commit(
        repo_dir=repo_dir,
        commit_message="checkpoint message",
    )

    assert payload == {
        "status": "committed",
        "sha": "def456",
        "error": "",
        "memory_only": "true",
    }
    assert ("add", "--", expected_memory_rel) in git_calls
    assert ("add", "-A") not in git_calls


def test_workflow_checkpoint_commit_returns_noop_when_limits_exceeded_without_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / ".git").mkdir()
    (repo_dir / "large.bin").write_bytes(b"x" * 8)

    git_calls: list[tuple[str, ...]] = []

    def fake_run(cmd, **kwargs):
        assert kwargs["cwd"] == str(repo_dir)
        git_args = tuple(cmd[1:])
        git_calls.append(git_args)
        if git_args == ("rev-parse", "--is-inside-work-tree"):
            return subprocess.CompletedProcess(cmd, 0, stdout="true\n", stderr="")
        if git_args == ("status", "--porcelain"):
            return subprocess.CompletedProcess(
                cmd, 0, stdout=" M large.bin\n", stderr=""
            )
        raise AssertionError(f"unexpected git args: {git_args}")

    monkeypatch.setattr(workflow_commands.shutil, "which", lambda name: "/usr/bin/git")
    monkeypatch.setattr(workflow_commands.subprocess, "run", fake_run)
    monkeypatch.setattr(workflow_commands, "_GIT_COMMIT_MAX_BYTES", 1)

    payload = workflow_commands._workflow_checkpoint_commit(
        repo_dir=repo_dir,
        commit_message="checkpoint message",
    )

    assert payload == {
        "status": "noop",
        "sha": "",
        "error": "",
        "memory_only": "false",
    }
    assert git_calls == [
        ("rev-parse", "--is-inside-work-tree"),
        ("status", "--porcelain"),
    ]
