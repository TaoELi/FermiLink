from __future__ import annotations

from pathlib import Path

import pytest

from fermilink import cli
from fermilink.agent_runtime import AgentRuntimePolicy
from fermilink.cli.commands import workflows as workflow_commands


def test_loop_reads_prompt_file_and_initializes_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    (repo_dir / "prompt.md").write_text("do the thing", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages"
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "pkg-a",
            "source": "default",
            "reason": "default_fallback",
            "note": "default_fallback",
        },
    )
    monkeypatch.setattr(
        cli,
        "_overlay_exec_package",
        lambda **_kwargs: {
            "linked_count": 1,
            "collision_count": 0,
            "linked_dependency_count": 0,
        },
    )
    captured: dict[str, object] = {}

    def fake_run_chat_turn(**kwargs):
        captured.update(kwargs)
        return {"assistant_text": "ok", "return_code": 0, "stderr": ""}

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_chat_turn)

    cleanup_calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        cli,
        "_cleanup_exec_overlay_symlinks",
        lambda *, repo_dir, workspace_root: cleanup_calls.append(
            (repo_dir, workspace_root)
        ),
    )

    code = cli.main(["loop", "--max-iterations", "1", "prompt.md"])
    assert code == 1

    memory_path = repo_dir / "projects" / "memory.md"
    assert memory_path.exists()
    memory = memory_path.read_text(encoding="utf-8")
    assert "do the thing" in memory
    assert "## Short-Term Memory (Operational)" in memory
    assert "### Plan" in memory
    assert "### Progress log" in memory
    assert "## Long-Term Memory (Persistent)" in memory
    assert "### File map" in memory
    assert "### Simulation history" in memory
    assert "### Key results" in memory
    assert "### Suggested skills updates" in memory

    assert "projects/memory.md" in str(captured.get("prompt"))
    assert "do the thing" in str(captured.get("prompt"))

    assert cleanup_calls
    out = capsys.readouterr().out
    assert "[loop] memory: projects/memory.md" in out


def test_loop_emits_done_token_when_present_in_last_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages"
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "pkg-a",
            "source": "default",
            "reason": "default_fallback",
            "note": "default_fallback",
        },
    )
    monkeypatch.setattr(
        cli,
        "_overlay_exec_package",
        lambda **_kwargs: {
            "linked_count": 1,
            "collision_count": 0,
            "linked_dependency_count": 0,
        },
    )
    monkeypatch.setattr(
        cli,
        "_run_exec_chat_turn",
        lambda **_kwargs: {
            "assistant_text": f"all done\n{cli.LOOP_DONE_TOKEN}\n",
            "return_code": 0,
            "stderr": "",
        },
    )
    monkeypatch.setattr(cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None)

    code = cli.main(["loop", "finish it"])
    assert code == 0
    out = capsys.readouterr().out
    assert cli.LOOP_DONE_TOKEN in out


def test_loop_parser_supports_package_pin_and_git_flags() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        [
            "loop",
            "hello",
            "--package",
            "maxwelllink",
            "--init-git",
            "--sandbox",
            "workspace-write",
        ]
    )
    assert args.package_id == "maxwelllink"
    assert args.init_git is True
    assert args.sandbox == "workspace-write"
    assert args.max_iterations == 10
    assert args.wait_seconds == 0.0
    assert args.max_wait_seconds == 600.0


def test_resolve_exec_like_user_prompt_accepts_long_single_token_text() -> None:
    long_prompt = "x" * 5000
    text, prompt_file = cli._resolve_exec_like_user_prompt(
        cli.argparse.Namespace(prompt=[long_prompt], command="loop")
    )
    assert text == long_prompt
    assert prompt_file is None


def test_loop_wait_seconds_sleeps_between_iterations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages"
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "pkg-a",
            "source": "default",
            "reason": "default_fallback",
            "note": "default_fallback",
        },
    )
    monkeypatch.setattr(
        cli,
        "_overlay_exec_package",
        lambda **_kwargs: {
            "linked_count": 1,
            "collision_count": 0,
            "linked_dependency_count": 0,
        },
    )

    run_calls: list[dict[str, object]] = []
    run_results = [
        {"assistant_text": "not done yet", "return_code": 0, "stderr": ""},
        {"assistant_text": cli.LOOP_DONE_TOKEN, "return_code": 0, "stderr": ""},
    ]

    def fake_run_chat_turn(**kwargs):
        run_calls.append(kwargs)
        return run_results[len(run_calls) - 1]

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_chat_turn)
    monkeypatch.setattr(cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None)

    slept: list[float] = []
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: slept.append(float(seconds)))

    code = cli.main(
        ["loop", "--max-iterations", "2", "--wait-seconds", "3", "finish it"]
    )
    assert code == 0
    assert len(run_calls) == 2
    assert slept == [3.0]


def test_loop_wait_seconds_uses_agent_tag_and_caps_by_max_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages"
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "pkg-a",
            "source": "default",
            "reason": "default_fallback",
            "note": "default_fallback",
        },
    )
    monkeypatch.setattr(
        cli,
        "_overlay_exec_package",
        lambda **_kwargs: {
            "linked_count": 1,
            "collision_count": 0,
            "linked_dependency_count": 0,
        },
    )

    run_results = [
        {
            "assistant_text": "working\n<wait_seconds>120</wait_seconds>\n",
            "return_code": 0,
            "stderr": "",
        },
        {"assistant_text": cli.LOOP_DONE_TOKEN, "return_code": 0, "stderr": ""},
    ]
    run_calls: list[dict[str, object]] = []

    def fake_run_chat_turn(**kwargs):
        run_calls.append(kwargs)
        return run_results[len(run_calls) - 1]

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_chat_turn)
    monkeypatch.setattr(cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None)

    slept: list[float] = []
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: slept.append(float(seconds)))

    code = cli.main(
        [
            "loop",
            "--max-iterations",
            "2",
            "--wait-seconds",
            "5",
            "--max-wait-seconds",
            "30",
            "finish it",
        ]
    )
    assert code == 0
    assert slept == [30.0]


def test_extract_loop_wait_seconds_returns_none_for_invalid_values() -> None:
    assert cli._extract_loop_wait_seconds("no token here") is None
    assert cli._extract_loop_wait_seconds("<wait_seconds>-1</wait_seconds>") is None
    assert cli._extract_loop_wait_seconds("<wait_seconds>abc</wait_seconds>") is None
    assert cli._extract_loop_wait_seconds("<wait_seconds>15</wait_seconds>") == 15.0


def test_ensure_loop_memory_upgrades_legacy_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    projects_dir = repo_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    memory_path = projects_dir / "memory.md"
    memory_path.write_text(
        (
            "# FermiLink Loop Memory\n\n"
            "- started_at_utc: 2026-01-01T00:00:00Z\n\n"
            "## Original request\nlegacy task\n\n"
            "## Plan\n- [ ] first step\n\n"
            "## Progress log\n- initialized\n"
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(repo_dir)
    result = cli._ensure_loop_memory(
        repo_dir=repo_dir,
        user_prompt="legacy task",
        prompt_file=None,
        overwrite=False,
    )

    assert result == memory_path
    upgraded = memory_path.read_text(encoding="utf-8")
    assert "## Short-Term Memory (Operational)" in upgraded
    assert "### Plan" in upgraded
    assert "- [ ] first step" in upgraded
    assert "### Progress log" in upgraded
    assert "## Long-Term Memory (Persistent)" in upgraded
    assert "### Suggested skills updates" in upgraded


def test_reset_loop_short_term_memory_preserves_long_term(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    memory_path = cli._ensure_loop_memory(
        repo_dir=repo_dir,
        user_prompt="initial request",
        prompt_file=None,
        overwrite=False,
    )
    baseline = memory_path.read_text(encoding="utf-8")
    customized = baseline.replace(
        "- [ ] (fill in a small checklist plan)", "- [x] previous checklist item"
    ).replace("- initialized", "- previous progress entry")
    customized = customized.replace(
        "- (result_id | metric | value | conditions | evidence_path)\n",
        "- (result_id | metric | value | conditions | evidence_path)\n"
        "- result-001 | test_metric | 1.0 | baseline | artifacts/result.txt\n",
    )
    memory_path.write_text(customized, encoding="utf-8")

    workflow_commands._reset_loop_short_term_memory(
        repo_dir=repo_dir,
        user_prompt="next task",
        prompt_file=None,
        workflow_context_lines=["- workflow: reproduce"],
    )

    updated = memory_path.read_text(encoding="utf-8")
    assert "- [ ] (fill in a small checklist plan)" in updated
    assert "- initialized" in updated
    assert "- [x] previous checklist item" not in updated
    assert "- previous progress entry" not in updated
    assert "- result-001 | test_metric | 1.0 | baseline | artifacts/result.txt" in updated
