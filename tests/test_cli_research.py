from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from fermilink import cli
from fermilink.cli.commands import workflows as workflow_commands


@pytest.fixture(autouse=True)
def _isolate_fermilink_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
    assert args.post_task_plan_audit is True
    opt_out_args = parser.parse_args(["research", "idea.md", "--no-post-task-plan-audit"])
    assert opt_out_args.post_task_plan_audit is False
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


# ---------------------------------------------------------------------------
# Research v2 (exploratory charter -> phase loop -> paper) behavioral tests.
# The legacy deterministic research path was replaced; these cover the new one.
# ---------------------------------------------------------------------------


def _research_task(task_id: str, executor: str = "loop", **overrides) -> dict:
    task = {
        "id": task_id,
        "phase": 1,
        "title": f"probe {task_id}",
        "executor": executor,
        "approach_id": "a1",
        "objective": "objective",
        "probe_question": "question",
        "methods": [],
        "parameter_constraints": [],
        "expected_evidence": [],
        "success_checks": [],
        "kill_checks": [],
        "plot_requirements": [],
        "prompt_markdown": f"do {task_id}",
    }
    task.update(overrides)
    return task


def _charter_plan(tasks: list[dict]) -> dict:
    return {
        "version": 2,
        "mode": "research",
        "paper_source": "idea.md",
        "central_question": "Does X hold?",
        "hypotheses": [{"id": "h1", "statement": "X holds", "status": "open"}],
        "approaches": [
            {
                "id": "a1",
                "summary": "approach one",
                "rationale": "because",
                "risks": ["risk"],
                "mitigations": ["mitigate"],
                "fallback": "fallback",
                "status": "candidate",
            }
        ],
        "success_criteria": ["publishable signal"],
        "kill_criteria": ["no signal"],
        "deliverable_kind": "paper",
        "assumptions": [],
        "phases": [
            {
                "index": 1,
                "goal": "learn",
                "approach_id": "a1",
                "task_ids": [t["id"] for t in tasks],
                "status": "planned",
                "findings_file": "",
                "decision": "",
            }
        ],
        "tasks": tasks,
    }


def _reflection(
    *, phase_index: int, decision: str, next_tasks: list[dict] | None = None, **extra
) -> dict:
    payload = {
        "version": 1,
        "phase_index": phase_index,
        "decision": decision,
        "reason": "reflection reason",
        "findings_markdown": "what happened",
        "belief_updates": [],
        "approach_updates": [],
        "deliverable_kind": "negative_result_paper"
        if decision == "declare_negative_result"
        else "paper",
        "next_phase": {
            "goal": "next",
            "approach_id": "a1",
            "tasks": next_tasks or [],
        },
        "findings_file": f"findings/phase_{phase_index:02d}_findings.md",
    }
    payload.update(extra)
    return payload


def test_research_parser_new_flag_defaults() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["research", "idea.md"])
    assert args.max_phases == 6
    assert args.enable_exploop is False
    assert args.enable_code is True
    assert args.enable_derivation is True
    assert args.allow_pivot is True
    assert args.proof_depth == "standard"
    assert args.charter_only is False

    tuned = parser.parse_args(
        [
            "research",
            "idea.md",
            "--charter-only",
            "--enable-exploop",
            "--no-enable-code",
            "--no-enable-derivation",
            "--no-allow-pivot",
            "--max-phases",
            "3",
            "--proof-depth",
            "publication",
        ]
    )
    assert tuned.charter_only is True
    assert tuned.enable_exploop is True
    assert tuned.enable_code is False
    assert tuned.enable_derivation is False
    assert tuned.allow_pivot is False
    assert tuned.max_phases == 3
    assert tuned.proof_depth == "publication"


def test_coerce_research_executor_gates_disabled_backends() -> None:
    assert (
        workflow_commands._coerce_research_executor("drvloop", {"loop", "drvloop"})
        == "drvloop"
    )
    assert workflow_commands._coerce_research_executor("exploop", {"loop"}) == "loop"
    assert workflow_commands._coerce_research_executor("bogus", {"loop", "code"}) == "loop"
    assert workflow_commands._coerce_research_executor(None, {"loop"}) == "loop"


def test_normalize_research_charter_builds_phase_one() -> None:
    raw = {
        "central_question": "Does X hold?",
        "approaches": [{"id": "a1", "summary": "s"}],
        "phase_1_tasks": [
            {
                "id": "task_001",
                "title": "t",
                "executor": "exploop",
                "prompt_markdown": "do it",
            }
        ],
    }
    plan = workflow_commands._normalize_research_charter(
        raw,
        source_description="idea.md",
        enabled_executors={"loop", "drvloop"},
    )
    assert plan["version"] == 2
    assert plan["central_question"] == "Does X hold?"
    assert plan["tasks"][0]["executor"] == "loop"  # exploop disabled -> loop
    assert plan["tasks"][0]["phase"] == 1
    assert plan["phases"][0]["task_ids"] == ["task_001"]


def test_normalize_research_charter_requires_question_and_tasks() -> None:
    with pytest.raises(cli.PackageError):
        workflow_commands._normalize_research_charter(
            {"phase_1_tasks": [{"id": "t", "prompt_markdown": "x"}]},
            source_description="idea.md",
            enabled_executors={"loop"},
        )
    with pytest.raises(cli.PackageError):
        workflow_commands._normalize_research_charter(
            {"central_question": "Q", "phase_1_tasks": []},
            source_description="idea.md",
            enabled_executors={"loop"},
        )


def test_normalize_research_reflection_decisions() -> None:
    downgraded = workflow_commands._normalize_research_reflection(
        {
            "decision": "revise_approach",
            "reason": "r",
            "next_phase": {"tasks": [{"id": "task_009", "prompt_markdown": "x"}]},
        },
        phase_index=1,
        allow_pivot=False,
        enabled_executors={"loop"},
        completed_ids={"task_001"},
    )
    assert downgraded["decision"] == "deepen"

    terminal = workflow_commands._normalize_research_reflection(
        {
            "decision": "converge_to_paper",
            "next_phase": {"tasks": [{"id": "x", "prompt_markdown": "y"}]},
        },
        phase_index=1,
        allow_pivot=True,
        enabled_executors={"loop"},
        completed_ids=set(),
    )
    assert terminal["next_phase"]["tasks"] == []

    negative = workflow_commands._normalize_research_reflection(
        {"decision": "declare_negative_result"},
        phase_index=1,
        allow_pivot=True,
        enabled_executors={"loop"},
        completed_ids=set(),
    )
    assert negative["deliverable_kind"] == "negative_result_paper"

    with pytest.raises(cli.PackageError):
        workflow_commands._normalize_research_reflection(
            {"decision": "not_a_decision"},
            phase_index=1,
            allow_pivot=True,
            enabled_executors={"loop"},
            completed_ids=set(),
        )


def test_generate_research_charter_uses_charter_prompts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    prompts: list[str] = []

    charter_payload = {
        "central_question": "Does X hold?",
        "approaches": [{"id": "a1", "summary": "s"}],
        "phase_1_tasks": [
            {"id": "task_001", "title": "t", "executor": "loop", "prompt_markdown": "go"}
        ],
    }

    def fake_exec_turn(**kwargs) -> dict[str, object]:
        prompts.append(str(kwargs.get("prompt") or ""))
        return {
            "return_code": 0,
            "assistant_text": "<research_plan>"
            + json.dumps(charter_payload)
            + "</research_plan>",
            "stderr": "",
        }

    monkeypatch.setattr(workflow_commands, "_run_reproduce_exec_turn", fake_exec_turn)
    plan = workflow_commands._generate_research_charter(
        repo_dir=repo_dir,
        run_dir=repo_dir,
        source_text="request",
        source_description="idea.md",
        requested_package_id=None,
        sandbox_override=None,
        provider_bin_override="codex",
        planner_max_tries=1,
        auditor_max_tries=1,
        enabled_executors={"loop", "code", "drvloop"},
    )
    assert plan["central_question"] == "Does X hold?"
    assert plan["tasks"][0]["executor"] == "loop"
    assert len(prompts) == 2
    assert "research charter mode" in prompts[0]
    assert "research charter audit mode" in prompts[1]
    assert "Enabled executors:" in prompts[0]


def test_research_charter_only_writes_charter_without_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_research_charter",
        lambda **_kwargs: _charter_plan([_research_task("task_001")]),
    )
    monkeypatch.setattr(
        cli,
        "_cmd_loop",
        lambda _args: (_ for _ in ()).throw(
            AssertionError("executor should not run in --charter-only")
        ),
    )

    assert cli.main(["research", "idea.md", "--charter-only"]) == 0

    runs_root = repo_dir / "projects" / "research"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    assert (run_dir / "plan.json").is_file()
    assert (run_dir / "charter.md").is_file()
    assert (run_dir / "prompts" / "task_001.md").is_file()
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "charter_ready"
    assert state["mode"] == "research"
    assert state["task_executors"]["task_001"] == "loop"


def test_research_runs_phase_then_converges_and_writes_paper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_research_charter",
        lambda **_kwargs: _charter_plan([_research_task("task_001")]),
    )

    loop_calls: list[Path] = []

    def fake_loop(loop_args) -> int:
        loop_calls.append(Path(str(loop_args.prompt[0])))
        return 0

    monkeypatch.setattr(cli, "_cmd_loop", fake_loop)

    reflect_calls: list[int] = []

    def fake_reflect(**kwargs) -> dict:
        reflect_calls.append(int(kwargs["phase_index"]))
        return _reflection(phase_index=int(kwargs["phase_index"]), decision="converge_to_paper")

    monkeypatch.setattr(cli, "_run_research_reflection", fake_reflect)

    paper_calls: list[dict] = []

    def fake_paper(**kwargs) -> dict:
        paper_calls.append(kwargs)
        return {
            "report_path": str(Path(kwargs["run_dir"]) / "paper.md"),
            "paper_path": str(Path(kwargs["run_dir"]) / "paper.md"),
            "deliverable_kind": "paper",
        }

    monkeypatch.setattr(cli, "_finalize_research_paper", fake_paper)

    assert cli.main(["research", "idea.md"]) == 0
    assert [p.name for p in loop_calls] == ["task_001.md"]
    assert reflect_calls == [1]
    assert len(paper_calls) == 1

    runs_root = repo_dir / "projects" / "research"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["decision"] == "converge_to_paper"


def test_research_reflection_replans_next_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_research_charter",
        lambda **_kwargs: _charter_plan([_research_task("task_001")]),
    )

    loop_calls: list[str] = []
    monkeypatch.setattr(
        cli,
        "_cmd_loop",
        lambda loop_args: loop_calls.append(Path(str(loop_args.prompt[0])).name) or 0,
    )

    def fake_reflect(**kwargs) -> dict:
        phase = int(kwargs["phase_index"])
        if phase == 1:
            return _reflection(
                phase_index=1,
                decision="advance",
                next_tasks=[_research_task("task_002", phase=2)],
            )
        return _reflection(phase_index=phase, decision="converge_to_paper")

    monkeypatch.setattr(cli, "_run_research_reflection", fake_reflect)
    monkeypatch.setattr(
        cli,
        "_finalize_research_paper",
        lambda **kwargs: {"report_path": "paper.md", "paper_path": "paper.md"},
    )

    assert cli.main(["research", "idea.md"]) == 0
    assert loop_calls == ["task_001.md", "task_002.md"]

    runs_root = repo_dir / "projects" / "research"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    assert [t["id"] for t in plan["tasks"]] == ["task_001", "task_002"]
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["phase_index"] == 2
    assert len(state["phases"]) == 2
    assert (run_dir / "prompts" / "task_002.md").is_file()


def test_research_reflection_receives_only_current_phase_tasks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_research_charter",
        lambda **_kwargs: _charter_plan([_research_task("task_001")]),
    )
    monkeypatch.setattr(cli, "_cmd_loop", lambda _a: 0)

    seen_phase_ids: list[list[str]] = []

    def fake_reflect(**kwargs) -> dict:
        seen_phase_ids.append(sorted(kwargs["phase_task_ids"]))
        phase = int(kwargs["phase_index"])
        if phase == 1:
            return _reflection(
                phase_index=1,
                decision="advance",
                next_tasks=[_research_task("task_002", phase=2)],
            )
        return _reflection(phase_index=phase, decision="converge_to_paper")

    monkeypatch.setattr(cli, "_run_research_reflection", fake_reflect)
    monkeypatch.setattr(
        cli,
        "_finalize_research_paper",
        lambda **kwargs: {"report_path": "paper.md", "paper_path": "paper.md"},
    )

    assert cli.main(["research", "idea.md"]) == 0
    # Phase 2 reflection must see ONLY task_002, not the cumulative [task_001, task_002].
    assert seen_phase_ids == [["task_001"], ["task_002"]]


def test_research_dispatches_executor_per_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_research_charter",
        lambda **_kwargs: _charter_plan(
            [
                _research_task("task_001", executor="loop"),
                _research_task("task_002", executor="drvloop"),
                _research_task("task_003", executor="exploop"),
            ]
        ),
    )

    loop_calls: list = []
    drv_calls: list = []
    exp_calls: list = []
    monkeypatch.setattr(cli, "_cmd_loop", lambda a: loop_calls.append(a) or 0)
    monkeypatch.setattr(cli, "_cmd_drvloop", lambda a: drv_calls.append(a) or 0)
    monkeypatch.setattr(cli, "_cmd_exploop", lambda a: exp_calls.append(a) or 0)
    monkeypatch.setattr(
        cli,
        "_run_research_reflection",
        lambda **kwargs: _reflection(
            phase_index=int(kwargs["phase_index"]), decision="converge_to_paper"
        ),
    )
    monkeypatch.setattr(
        cli,
        "_finalize_research_paper",
        lambda **kwargs: {"report_path": "paper.md", "paper_path": "paper.md"},
    )

    assert (
        cli.main(
            ["research", "idea.md", "--enable-exploop", "--proof-depth", "publication"]
        )
        == 0
    )
    assert len(loop_calls) == 1
    assert len(drv_calls) == 1
    assert len(exp_calls) == 1
    assert drv_calls[0].command == "drvloop"
    assert drv_calls[0].proof_depth == "publication"
    assert exp_calls[0].command == "exploop"
    # exploop/drvloop receive a folded prompt (text), not a bare file path.
    assert "research" in str(drv_calls[0].prompt[0]).lower()


def test_research_exploop_downgraded_when_not_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    # Charter itself would coerce, but simulate a plan that still carries exploop.
    plan = _charter_plan([_research_task("task_001", executor="exploop")])
    plan["tasks"][0]["executor"] = "exploop"
    monkeypatch.setattr(cli, "_generate_research_charter", lambda **_kwargs: plan)

    loop_calls: list = []
    monkeypatch.setattr(cli, "_cmd_loop", lambda a: loop_calls.append(a) or 0)
    monkeypatch.setattr(
        cli,
        "_cmd_exploop",
        lambda a: (_ for _ in ()).throw(
            AssertionError("exploop must not run without --enable-exploop")
        ),
    )
    monkeypatch.setattr(
        cli,
        "_run_research_reflection",
        lambda **kwargs: _reflection(
            phase_index=int(kwargs["phase_index"]), decision="converge_to_paper"
        ),
    )
    monkeypatch.setattr(
        cli,
        "_finalize_research_paper",
        lambda **kwargs: {"report_path": "paper.md", "paper_path": "paper.md"},
    )

    assert cli.main(["research", "idea.md"]) == 0  # exploop NOT enabled
    assert len(loop_calls) == 1  # downgraded to loop


def test_research_probe_failure_is_tolerated_and_advances(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_research_charter",
        lambda **_kwargs: _charter_plan([_research_task("task_001")]),
    )

    loop_calls: list[int] = []
    monkeypatch.setattr(
        cli, "_cmd_loop", lambda _a: loop_calls.append(1) or 1
    )  # always incomplete
    monkeypatch.setattr(
        cli,
        "_run_research_reflection",
        lambda **kwargs: _reflection(
            phase_index=int(kwargs["phase_index"]), decision="converge_to_paper"
        ),
    )
    monkeypatch.setattr(
        cli,
        "_finalize_research_paper",
        lambda **kwargs: {"report_path": "paper.md", "paper_path": "paper.md"},
    )

    assert cli.main(["research", "idea.md", "--task-max-runs", "2"]) == 0
    assert len(loop_calls) == 2  # retried up to task-max-runs, then advanced

    runs_root = repo_dir / "projects" / "research"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["tasks"][0]["status"] == "failed"


def test_research_max_phases_forces_convergence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_research_charter",
        lambda **_kwargs: _charter_plan([_research_task("task_001")]),
    )
    monkeypatch.setattr(cli, "_cmd_loop", lambda _a: 0)

    reflect_phases: list[int] = []
    counter = {"n": 0}

    def never_terminal(**kwargs) -> dict:
        reflect_phases.append(int(kwargs["phase_index"]))
        counter["n"] += 1
        return _reflection(
            phase_index=int(kwargs["phase_index"]),
            decision="advance",
            next_tasks=[_research_task(f"task_x{counter['n']:03d}", phase=99)],
        )

    monkeypatch.setattr(cli, "_run_research_reflection", never_terminal)
    monkeypatch.setattr(
        cli,
        "_finalize_research_paper",
        lambda **kwargs: {"report_path": "paper.md", "paper_path": "paper.md"},
    )

    assert cli.main(["research", "idea.md", "--max-phases", "2"]) == 0
    assert reflect_phases == [1, 2]  # stops at max phases

    runs_root = repo_dir / "projects" / "research"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["decision"] == "converge_to_paper"


def test_research_abort_returns_nonzero_without_paper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_research_charter",
        lambda **_kwargs: _charter_plan([_research_task("task_001")]),
    )
    monkeypatch.setattr(cli, "_cmd_loop", lambda _a: 0)
    monkeypatch.setattr(
        cli,
        "_run_research_reflection",
        lambda **kwargs: _reflection(
            phase_index=int(kwargs["phase_index"]), decision="abort"
        ),
    )
    monkeypatch.setattr(
        cli,
        "_finalize_research_paper",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("paper must not be written on abort")
        ),
    )

    assert cli.main(["research", "idea.md"]) != 0

    runs_root = repo_dir / "projects" / "research"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert state["decision"] == "abort"


def test_research_negative_result_sets_deliverable_kind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_research_charter",
        lambda **_kwargs: _charter_plan([_research_task("task_001")]),
    )
    monkeypatch.setattr(cli, "_cmd_loop", lambda _a: 0)
    monkeypatch.setattr(
        cli,
        "_run_research_reflection",
        lambda **kwargs: _reflection(
            phase_index=int(kwargs["phase_index"]),
            decision="declare_negative_result",
        ),
    )

    paper_calls: list = []
    monkeypatch.setattr(
        cli,
        "_finalize_research_paper",
        lambda **kwargs: paper_calls.append(kwargs)
        or {"report_path": "paper.md", "paper_path": "paper.md"},
    )

    assert cli.main(["research", "idea.md"]) == 0
    assert len(paper_calls) == 1

    runs_root = repo_dir / "projects" / "research"
    latest_run = (runs_root / "latest_run.txt").read_text(encoding="utf-8").strip()
    run_dir = runs_root / latest_run
    plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    assert plan["deliverable_kind"] == "negative_result_paper"


def test_research_status_hook_emits_phase_progress(
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
        "_generate_research_charter",
        lambda **_kwargs: _charter_plan([_research_task("task_001")]),
    )

    def fake_loop(loop_args) -> int:
        hook = getattr(loop_args, "_fermilink_loop_iteration_hook", None)
        if callable(hook):
            hook(2, 10)
        return 0

    monkeypatch.setattr(cli, "_cmd_loop", fake_loop)
    monkeypatch.setattr(
        cli,
        "_run_research_reflection",
        lambda **kwargs: _reflection(
            phase_index=int(kwargs["phase_index"]), decision="converge_to_paper"
        ),
    )
    monkeypatch.setattr(cli, "_finalize_research_paper", lambda **kwargs: {})

    parser = cli._build_parser()
    args = parser.parse_args(["research", "idea.md"])
    setattr(
        args,
        "_fermilink_workflow_status_hook",
        lambda mode_text: status_updates.append(str(mode_text)),
    )
    assert cli._cmd_research(args) == 0
    assert any(
        s == "research phase task 1/1 [loop] loop 2/10" for s in status_updates
    )


def test_research_completion_checkpoint_commit_invoked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli,
        "_generate_research_charter",
        lambda **_kwargs: _charter_plan([_research_task("task_001")]),
    )

    completion_calls: list = []
    monkeypatch.setattr(
        workflow_commands,
        "_workflow_completion_commit",
        lambda *, repo_dir, mode_name: completion_calls.append(
            (Path(repo_dir), str(mode_name))
        )
        or {"status": "noop", "sha": "", "error": "", "memory_only": "false"},
    )

    assert cli.main(["research", "idea.md", "--charter-only"]) == 0
    assert completion_calls == [(repo_dir, "research")]


def test_research_report_only_conflicts_with_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")
    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    assert cli.main(["research", "idea.md", "--report-only", "--restart"]) == 2


def test_research_report_only_requires_existing_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")
    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)

    assert cli.main(["research", "idea.md", "--report-only"]) == 2
    assert "requires an existing resumable research run" in capsys.readouterr().err


def test_research_report_only_finalizes_from_existing_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "idea.md").write_text("research request", encoding="utf-8")
    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)

    import hashlib

    fingerprint = hashlib.sha256("research request".encode("utf-8")).hexdigest()
    runs_root = repo_dir / "projects" / "research"
    run_dir = runs_root / "20240101-000000"
    run_dir.mkdir(parents=True, exist_ok=True)
    (runs_root / "latest_run.txt").write_text("20240101-000000\n", encoding="utf-8")
    state = {
        "version": 2,
        "run_id": "20240101-000000",
        "mode": "research",
        "status": "running_tasks",
        "source_fingerprint": fingerprint,
        "tasks": [{"id": "task_001", "title": "t", "prompt_file": "prompts/task_001.md"}],
        "task_runs": {"task_001": 1},
        "hpc_context": {"enabled": False, "mode": "local"},
    }
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (run_dir / "plan.json").write_text(
        json.dumps({"version": 2, "deliverable_kind": "paper", "tasks": state["tasks"]}),
        encoding="utf-8",
    )

    paper_calls: list = []
    monkeypatch.setattr(
        cli,
        "_finalize_research_paper",
        lambda **kwargs: paper_calls.append(kwargs)
        or {"report_path": "paper.md", "paper_path": "paper.md"},
    )

    assert cli.main(["research", "idea.md", "--report-only"]) == 0
    assert len(paper_calls) == 1
    updated = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert "report" in updated


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
