from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from fermilink import cli
from fermilink.agent_runtime import AgentRuntimePolicy


def test_chat_runs_multiround_with_history_and_package_switch(
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

    selection_calls: list[dict[str, object]] = []
    selection_results = [
        {
            "package_id": "pkg-a",
            "source": "auto",
            "reason": "matched",
            "note": "matched",
        },
        {
            "package_id": "pkg-b",
            "source": "second_guess",
            "reason": "matched",
            "note": "second_guess_switch(pkg-a->pkg-b, conf=0.92)",
        },
    ]

    def fake_select(**kwargs):
        selection_calls.append(kwargs)
        return selection_results[len(selection_calls) - 1]

    monkeypatch.setattr(cli, "_resolve_exec_package_selection", fake_select)

    overlay_calls: list[str] = []
    monkeypatch.setattr(
        cli,
        "_overlay_exec_package",
        lambda **kwargs: (
            overlay_calls.append(str(kwargs["package_id"]))
            or {"linked_count": 2, "collision_count": 0, "linked_dependency_count": 1}
        ),
    )

    run_calls: list[dict[str, object]] = []
    run_results = [
        {"assistant_text": "assistant one", "return_code": 0, "stderr": ""},
        {"assistant_text": "assistant two", "return_code": 0, "stderr": ""},
    ]
    monkeypatch.setattr(
        cli,
        "_run_exec_chat_turn",
        lambda **kwargs: run_calls.append(kwargs) or run_results[len(run_calls) - 1],
    )

    cleanup_calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        cli,
        "_cleanup_exec_overlay_symlinks",
        lambda *, repo_dir, workspace_root: cleanup_calls.append(
            (repo_dir, workspace_root)
        ),
    )

    web_app = SimpleNamespace(
        _build_prompt=lambda history, text: (
            " | ".join(f"{role}:{content}" for role, content in history)
            + (" | " if history else "")
            + f"user:{text}"
        ),
        _append_history=lambda history, role, content: history + [(role, content)],
    )
    monkeypatch.setattr(cli, "_load_web_router_module", lambda: web_app)

    user_inputs = iter(["first prompt", "second prompt", "exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))

    code = cli.main(["chat"])
    assert code == 0

    assert len(selection_calls) == 2
    assert selection_calls[0]["current_package_id"] is None
    assert selection_calls[0]["current_source"] == cli.PACKAGE_SOURCE_NONE
    assert selection_calls[1]["current_package_id"] == "pkg-a"
    assert selection_calls[1]["current_source"] == "auto"

    assert overlay_calls == ["pkg-a", "pkg-b"]
    assert len(run_calls) == 2
    assert "projects/memory.md" in str(run_calls[0]["prompt"])
    assert "user:first prompt" in str(run_calls[0]["prompt"])
    assert "assistant:assistant one | user:second prompt" in str(run_calls[1]["prompt"])
    assert cleanup_calls == [(repo_dir, repo_dir), (repo_dir, repo_dir)]
    memory_path = repo_dir / "projects" / "memory.md"
    assert memory_path.is_file()
    assert "first prompt" in memory_path.read_text(encoding="utf-8")

    output = capsys.readouterr().out
    assert "[chat] Interactive mode." in output
    assert "[package] Using pkg-a (selection: auto)" in output
    assert "[package] Using pkg-b (selection: second_guess)" in output
    assert "Assistant> assistant one" in output
    assert "Assistant> assistant two" in output


def test_chat_enforces_sandbox_override_for_session(
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
            sandbox_policy="bypass",
            sandbox_mode="workspace-write",
            model="gpt-5.3-codex-xhigh",
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
        "_load_web_router_module",
        lambda: SimpleNamespace(
            _build_prompt=lambda history, text: text,
            _append_history=lambda history, role, content: history + [(role, content)],
        ),
    )
    monkeypatch.setattr(
        cli,
        "_cleanup_exec_overlay_symlinks",
        lambda **_kwargs: None,
    )

    captured: dict[str, object] = {}

    def fake_run_chat_turn(**kwargs):
        captured.update(kwargs)
        return {"assistant_text": "ok", "return_code": 0, "stderr": ""}

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_chat_turn)

    user_inputs = iter(["run one", "exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))

    code = cli.main(["chat", "--sandbox", "read-only"])
    assert code == 0
    assert captured["sandbox_policy"] == "enforce"
    assert captured["sandbox"] == "read-only"
    assert captured["model"] == "gpt-5.3-codex-xhigh"
    assert "projects/memory.md" in str(captured.get("prompt"))


def test_chat_parser_supports_package_pin_and_git_flags() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        [
            "chat",
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


def test_resolve_exec_package_selection_sticky_keeps_current_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli,
        "load_registry",
        lambda _root: {
            "packages": {"pkg-a": {"id": "pkg-a"}, "pkg-b": {"id": "pkg-b"}},
            "active_package": "pkg-a",
        },
    )
    monkeypatch.setattr(cli, "EXEC_SECOND_GUESS_ENABLED", False)

    web_app = SimpleNamespace(
        _normalize_package_id_safe=lambda value: (
            value if isinstance(value, str) else None
        ),
        _load_router_config=lambda _root: {"default_package_id": "pkg-a"},
        _route_package_candidate=lambda **_kwargs: {
            "selected_package_id": "pkg-b",
            "reason": "matched",
            "margin": 1,
        },
        _resolve_default_package_id=lambda **_kwargs: "pkg-a",
        PACKAGE_ROUTER_STICKY=True,
        PACKAGE_ROUTER_SWITCH_MARGIN=2,
    )
    monkeypatch.setattr(cli, "_load_web_router_module", lambda: web_app)

    result = cli._resolve_exec_package_selection(
        user_prompt="switch me",
        scipkg_root=tmp_path / "scientific_packages",
        repo_dir=tmp_path / "repo",
        requested_package_id=None,
        provider="codex",
        provider_bin="codex",
        sandbox_policy="enforce",
        current_package_id="pkg-a",
        current_source=cli.PACKAGE_SOURCE_AUTO,
    )

    assert result["package_id"] == "pkg-a"
    assert result["source"] == cli.PACKAGE_SOURCE_AUTO
    assert result["reason"] == "sticky_keep_current"
