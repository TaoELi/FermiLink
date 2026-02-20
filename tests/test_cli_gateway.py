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

    gateway_commands._save_gateway_state(state_path, state)
    loaded = gateway_commands._load_gateway_state(state_path)
    loaded_chat = gateway_commands._ensure_chat_state(
        gateway_commands._telegram_state(loaded), "telegram:42"
    )

    assert loaded_chat["active_workspace_id"] == workspace["id"]
    assert len(loaded_chat["workspaces"]) == 1
    assert loaded_chat["workspaces"][0]["label"] == "main"


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
