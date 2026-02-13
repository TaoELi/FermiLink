from __future__ import annotations

from pathlib import Path

import pytest

from fermilink import cli
from fermilink.agent_runtime import AgentRuntimePolicy


def test_loop_reads_prompt_file_and_initializes_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    (repo_dir / "prompt.md").write_text("do the thing", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages")
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
        lambda **_kwargs: {"linked_count": 1, "collision_count": 0, "linked_dependency_count": 0},
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
        lambda *, repo_dir, workspace_root: cleanup_calls.append((repo_dir, workspace_root)),
    )

    code = cli.main(["loop", "--max-iterations", "1", "prompt.md"])
    assert code == 1

    memory_path = repo_dir / "projects" / "memory.md"
    assert memory_path.exists()
    memory = memory_path.read_text(encoding="utf-8")
    assert "do the thing" in memory

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
    monkeypatch.setattr(cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages")
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
        lambda **_kwargs: {"linked_count": 1, "collision_count": 0, "linked_dependency_count": 0},
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


def test_resolve_loop_user_prompt_accepts_long_single_token_text() -> None:
    long_prompt = "x" * 5000
    text, prompt_file = cli._resolve_loop_user_prompt(
        cli.argparse.Namespace(prompt=[long_prompt])
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
    monkeypatch.setattr(cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages")
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
        lambda **_kwargs: {"linked_count": 1, "collision_count": 0, "linked_dependency_count": 0},
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

    code = cli.main(["loop", "--max-iterations", "2", "--wait-seconds", "3", "finish it"])
    assert code == 0
    assert len(run_calls) == 2
    assert slept == [3.0]
