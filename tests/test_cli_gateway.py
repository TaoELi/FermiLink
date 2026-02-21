from __future__ import annotations

import time
from pathlib import Path

from fermilink import cli
from fermilink.cli.commands import gateway as gateway_commands


def _loop_config() -> gateway_commands.GatewayLoopConfig:
    return gateway_commands.GatewayLoopConfig(
        package_id=None,
        sandbox=None,
        codex_bin="codex",
        max_iterations=2,
        wait_seconds=0.0,
        max_wait_seconds=10.0,
        pid_stall_seconds=0.0,
        init_git=True,
    )


def test_gateway_parser_supports_loop_forwarding_flags() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        [
            "gateway",
            "--telegram-token",
            "token-123",
            "--allow-from",
            "12345,alice",
            "--allow-from",
            "67890",
            "--package",
            "maxwelllink",
            "--sandbox",
            "read-only",
            "--max-iterations",
            "7",
            "--wait-seconds",
            "2",
            "--max-wait-seconds",
            "60",
            "--pid-stall-seconds",
            "11",
            "--no-init-git",
        ]
    )
    assert args.telegram_token == "token-123"
    assert args.allow_from == ["12345,alice", "67890"]
    assert args.package_id == "maxwelllink"
    assert args.sandbox == "read-only"
    assert args.max_iterations == 7
    assert args.wait_seconds == 2.0
    assert args.max_wait_seconds == 60.0
    assert args.pid_stall_seconds == 11.0
    assert args.init_git is False


def test_gateway_state_round_trip_preserves_active_workspace(tmp_path: Path) -> None:
    state_path = tmp_path / "chat_sessions.json"
    state = gateway_commands._default_gateway_state()
    telegram = gateway_commands._telegram_state(state)
    chat_state = gateway_commands._ensure_chat_state(telegram, "telegram:42")
    workspace = gateway_commands._create_workspace(
        chat_state,
        chat_id="42",
        requested_label="main",
        created_via="new",
    )
    chat_state["execution_mode"] = "exec"
    chat_state["last_run_status"] = "done"
    chat_state["last_run_reason"] = "exec_completed"
    chat_state["last_run_exit_code"] = 0
    chat_state["last_run_mode"] = "exec"

    gateway_commands._save_gateway_state(state_path, state)
    loaded = gateway_commands._load_gateway_state(state_path)
    loaded_chat = gateway_commands._ensure_chat_state(
        gateway_commands._telegram_state(loaded), "telegram:42"
    )

    assert loaded_chat["active_workspace_id"] == workspace["id"]
    assert len(loaded_chat["workspaces"]) == 1
    assert loaded_chat["workspaces"][0]["label"] == "main"
    assert loaded_chat["execution_mode"] == "exec"
    assert loaded_chat["last_run_status"] == "done"
    assert loaded_chat["last_run_reason"] == "exec_completed"
    assert loaded_chat["last_run_exit_code"] == 0
    assert loaded_chat["last_run_mode"] == "exec"


def test_handle_telegram_text_supports_sticky_new_and_use(tmp_path: Path) -> None:
    state = gateway_commands._default_gateway_state()
    workspaces_root = tmp_path / "workspaces"
    run_paths: list[Path] = []

    def fake_repo_ensurer(repo_dir: Path, _init_git: bool) -> None:
        projects_dir = repo_dir / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        (projects_dir / "scf_result.json").write_text("{}", encoding="utf-8")
        (repo_dir / "projects" / "memory.md").write_text(
            (
                "# FermiLink Unified Memory\n\n"
                "### Plan\n"
                "- [x] Build PySCF input for H2O\n"
                "- [x] Run SCF with HF/3-21g\n"
                "- [ ] Post-check convergence trend\n\n"
                "### Key results\n"
                "- h2o_final_energy | RHF total energy (Hartree) | -75.5854 | "
                "H2O basis 3-21g | projects/scf_result.json\n"
            ),
            encoding="utf-8",
        )

    def fake_loop_runner(
        repo_dir: Path,
        _prompt: str,
        _loop_config: gateway_commands.GatewayLoopConfig,
    ) -> tuple[int, dict[str, object]]:
        run_paths.append(repo_dir)
        return 0, {"status": "done", "reason": "done_token"}

    chat_id = "42"
    chat_key = "telegram:42"
    loop_config = _loop_config()

    first = gateway_commands._handle_telegram_text(
        text="simulate baseline",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    second = gateway_commands._handle_telegram_text(
        text="continue baseline",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    new_reply = gateway_commands._handle_telegram_text(
        text="/new branch",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    third = gateway_commands._handle_telegram_text(
        text="run branch workflow",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    use_reply = gateway_commands._handle_telegram_text(
        text="/use main",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    fourth = gateway_commands._handle_telegram_text(
        text="continue baseline again",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )

    assert "Run complete in workspace" in first
    assert "The requested simulation workflow finished successfully." in first
    assert "<b>What Was Done</b>" in first
    assert "<b>Key Findings</b>" in first
    assert "<b>Recent Artifacts</b>" in first
    assert "Run complete in workspace" in second
    assert "Switched to new workspace" in new_reply
    assert "Run complete in workspace" in third
    assert "Switched workspace" in use_reply
    assert "Run complete in workspace" in fourth

    assert len(run_paths) == 4
    assert run_paths[0] == run_paths[1]
    assert run_paths[2] != run_paths[0]
    assert run_paths[3] == run_paths[0]


def test_handle_telegram_text_mode_switches_between_loop_and_exec(
    tmp_path: Path,
) -> None:
    state = gateway_commands._default_gateway_state()
    workspaces_root = tmp_path / "workspaces"
    loop_calls: list[Path] = []
    exec_calls: list[Path] = []

    def fake_repo_ensurer(repo_dir: Path, _init_git: bool) -> None:
        (repo_dir / "projects").mkdir(parents=True, exist_ok=True)
        (repo_dir / "projects" / "memory.md").write_text(
            (
                "# FermiLink Unified Memory\n\n"
                "### Plan\n"
                "- [x] Execute request\n\n"
                "### Key results\n"
                "- energy | value | -1.0 | test | projects/result.json\n"
            ),
            encoding="utf-8",
        )

    def fake_loop_runner(
        repo_dir: Path,
        _prompt: str,
        _loop_config: gateway_commands.GatewayLoopConfig,
    ) -> tuple[int, dict[str, object]]:
        loop_calls.append(repo_dir)
        return 0, {"status": "done", "reason": "done_token"}

    def fake_exec_runner(
        repo_dir: Path,
        _prompt: str,
        _loop_config: gateway_commands.GatewayLoopConfig,
    ) -> tuple[int, dict[str, object]]:
        exec_calls.append(repo_dir)
        return 0, {"status": "done", "reason": "exec_completed"}

    chat_id = "501"
    chat_key = "telegram:501"
    loop_config = _loop_config()

    mode_before = gateway_commands._handle_telegram_text(
        text="/mode",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        exec_runner=fake_exec_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    set_exec = gateway_commands._handle_telegram_text(
        text="/mode exec",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        exec_runner=fake_exec_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    exec_run = gateway_commands._handle_telegram_text(
        text="run once",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        exec_runner=fake_exec_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    set_loop = gateway_commands._handle_telegram_text(
        text="/mode loop",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        exec_runner=fake_exec_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    loop_run = gateway_commands._handle_telegram_text(
        text="run iterative",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        exec_runner=fake_exec_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    where = gateway_commands._handle_telegram_text(
        text="/where",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        exec_runner=fake_exec_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )

    assert "Current mode: loop" in mode_before
    assert "Execution mode set to exec." in set_exec
    assert "Execution mode: <code>exec</code>." in exec_run
    assert "Single-turn execution finished successfully." in exec_run
    assert "Execution mode set to loop." in set_loop
    assert "Execution mode: <code>loop</code>." in loop_run
    assert "Current mode: loop" in where
    assert len(exec_calls) == 1
    assert len(loop_calls) == 1
    assert exec_calls[0] == loop_calls[0]


def test_status_reports_online_mode_workspace_and_last_run(tmp_path: Path) -> None:
    state = gateway_commands._default_gateway_state()
    workspaces_root = tmp_path / "workspaces"

    def fake_repo_ensurer(repo_dir: Path, _init_git: bool) -> None:
        (repo_dir / "projects").mkdir(parents=True, exist_ok=True)
        (repo_dir / "projects" / "memory.md").write_text(
            (
                "# FermiLink Unified Memory\n\n"
                "### Plan\n"
                "- [x] Run task\n\n"
                "### Key results\n"
                "- energy | value | -1.23 | test | projects/result.json\n"
            ),
            encoding="utf-8",
        )

    def fake_loop_runner(
        repo_dir: Path,
        _prompt: str,
        _loop_config: gateway_commands.GatewayLoopConfig,
    ) -> tuple[int, dict[str, object]]:
        return 0, {"status": "done", "reason": "done_token"}

    chat_id = "601"
    chat_key = "telegram:601"
    loop_config = _loop_config()

    before = gateway_commands._handle_telegram_text(
        text="/status",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    run_reply = gateway_commands._handle_telegram_text(
        text="run status test",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    after = gateway_commands._handle_telegram_text(
        text="/status",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )

    assert "<b>Gateway Status</b>" in before
    assert "No completed run recorded yet for this chat." in before
    assert "Execution mode: <code>loop</code>." in run_reply
    assert "<b>Last Run</b>" in after
    assert "Status: <code>done</code>" in after
    assert "Reason: done token" in after
    assert "Started: <code>" in after
    assert "Finished: <code>" in after


def test_status_reports_running_job_details_for_immediate_polling() -> None:
    state = gateway_commands._default_gateway_state()
    telegram = gateway_commands._telegram_state(state)
    chat_id = "777"
    chat_key = "telegram:777"
    chat_state = gateway_commands._ensure_chat_state(telegram, chat_key)

    job, queued_reply = gateway_commands._queue_telegram_run(
        text="simulate h2o energy with pyscf",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
    )
    chat_state = gateway_commands._ensure_chat_state(telegram, chat_key)
    assert "Run queued and starting shortly." in queued_reply
    assert chat_state["pending_run_count"] == 1
    assert chat_state["is_running"] is False

    gateway_commands._mark_chat_job_running(chat_state, job)
    status = gateway_commands._build_status_message(chat_state)

    assert "Agent: <b>running</b>" in status
    assert "<b>Current Run</b>" in status
    assert "Mode: <code>loop</code>" in status
    assert "Prompt: simulate h2o energy with pyscf" in status


def test_status_reports_queued_when_requests_waiting() -> None:
    state = gateway_commands._default_gateway_state()
    chat_id = "778"
    chat_key = "telegram:778"

    _, first = gateway_commands._queue_telegram_run(
        text="first run",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
    )
    _, second = gateway_commands._queue_telegram_run(
        text="second run",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
    )

    telegram = gateway_commands._telegram_state(state)
    chat_state = gateway_commands._ensure_chat_state(telegram, chat_key)
    status = gateway_commands._build_status_message(chat_state)

    assert "Run queued and starting shortly." in first
    assert "Queued request in workspace" in second
    assert "Queue position: <code>2</code>" in second
    assert "Agent: <b>queued</b> (2 pending)" in status
    assert "<b>Current Run</b>" not in status


def test_collect_media_for_run_reply_prefers_memory_and_recent_files(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    projects_dir = repo_dir / "projects"
    outputs_dir = repo_dir / "outputs"
    projects_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    key_image = projects_dir / "scf_convergence.png"
    key_image.write_bytes(b"png")
    key_doc = projects_dir / "report.pdf"
    key_doc.write_bytes(b"pdf")
    recent_image = outputs_dir / "recent.png"
    recent_image.write_bytes(b"png")

    (projects_dir / "memory.md").write_text(
        (
            "# FermiLink Unified Memory\n\n"
            "### Key results\n"
            "- k1 | SCF convergence figure | generated | run | projects/scf_convergence.png\n"
            "- k2 | report | generated | run | projects/report.pdf\n"
        ),
        encoding="utf-8",
    )

    now = time.time()
    old = now - 120
    newer = now - 1
    # Keep key artifacts old to validate memory-evidence inclusion.
    key_image.touch()
    key_doc.touch()
    recent_image.touch()
    key_image_mtime = old
    key_doc_mtime = old
    recent_mtime = newer
    import os

    os.utime(key_image, (key_image_mtime, key_image_mtime))
    os.utime(key_doc, (key_doc_mtime, key_doc_mtime))
    os.utime(recent_image, (recent_mtime, recent_mtime))

    images, docs = gateway_commands._collect_media_for_run_reply(
        repo_dir, run_started_epoch=now - 10
    )
    assert key_image in images
    assert recent_image in images
    assert key_doc in docs


def test_split_key_result_item_handles_labeled_values_with_pipe_markers() -> None:
    item = (
        "result_id: run2_ez | metric: final Ez spatial field | "
        "value: shape 80x80, max |Ez| | conditions: 2d bragg | "
        "evidence_path: projects/ez_field_final.png"
    )
    result_id, metric, value, conditions, evidence_path = (
        gateway_commands._split_key_result_item(item)
    )
    assert result_id == "run2_ez"
    assert metric == "final Ez spatial field"
    assert "shape 80x80, max" in value
    assert "Ez" in value
    assert conditions == "2d bragg"
    assert evidence_path == "projects/ez_field_final.png"


def test_run_summary_deduplicates_key_findings_by_metric_keep_latest(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    projects_dir = repo_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    (projects_dir / "memory.md").write_text(
        (
            "# FermiLink Unified Memory\n\n"
            "### Key results\n"
            "- result_id: run1_ez | metric: final Ez spatial field | "
            "value: shape 80x80, max |Ez| | conditions: old run | "
            "evidence_path: projects/old_ez.png\n"
            "- result_id: run1_pe | metric: Pe(t) | value: Pe_final=3.059e-4 | "
            "conditions: old run | evidence_path: projects/old_pe.csv\n"
            "- result_id: run2_ez | metric: final Ez spatial field | "
            "value: shape 80, max |Ez| | conditions: latest run | "
            "evidence_path: projects/new_ez.png\n"
            "- result_id: run2_pe | metric: Pe(t) | value: Pe_final=4.553e-4 | "
            "conditions: latest run | evidence_path: projects/new_pe.csv\n"
        ),
        encoding="utf-8",
    )

    summary = gateway_commands._build_run_summary_message(
        mode="exec",
        workspace={"id": "mxl", "label": "mxl"},
        repo_dir=repo_dir,
        code=0,
        outcome={"status": "done", "reason": "exec_completed"},
    )

    assert summary.count("final Ez spatial field:") == 1
    assert summary.count("Pe(t):") == 1
    assert "latest run" in summary
    assert "old run" not in summary
