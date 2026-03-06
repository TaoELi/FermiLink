from __future__ import annotations

from pathlib import Path

import pytest

from fermilink.agent_runtime import AgentRuntimePolicy
from fermilink.runner import app as runner_app


def test_resolve_run_policy_honors_bypass_policy(monkeypatch) -> None:
    monkeypatch.setattr(
        runner_app,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="bypass",
            sandbox_mode="workspace-write",
            model="gpt-5.3-codex",
            reasoning_effort="high",
        ),
    )

    req = runner_app.RunRequest(user_prompt="hello", sandbox="read-only")
    provider, sandbox_policy, sandbox_mode, model, reasoning_effort = (
        runner_app._resolve_run_policy(req)
    )

    assert provider == "codex"
    assert sandbox_policy == "bypass"
    assert sandbox_mode is None
    assert model == "gpt-5.3-codex"
    assert reasoning_effort == "high"


def test_resolve_run_policy_allows_read_only_override(monkeypatch) -> None:
    monkeypatch.setattr(
        runner_app,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )

    req = runner_app.RunRequest(user_prompt="hello", sandbox="read-only")
    provider, sandbox_policy, sandbox_mode, model, reasoning_effort = (
        runner_app._resolve_run_policy(req)
    )

    assert provider == "codex"
    assert sandbox_policy == "enforce"
    assert sandbox_mode == "read-only"
    assert model is None
    assert reasoning_effort is None


def test_resolve_run_policy_ignores_request_provider_override(monkeypatch) -> None:
    monkeypatch.setattr(
        runner_app,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )

    req = runner_app.RunRequest(user_prompt="hello", provider="gemini")
    provider, sandbox_policy, sandbox_mode, model, reasoning_effort = (
        runner_app._resolve_run_policy(req)
    )

    assert provider == "codex"
    assert sandbox_policy == "enforce"
    assert sandbox_mode == "workspace-write"
    assert model is None
    assert reasoning_effort is None


# ---------------------------------------------------------------------------
# _ensure_claude_md_symlink
# ---------------------------------------------------------------------------


def test_ensure_claude_md_symlink_creates_symlink(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("policy")
    runner_app._ensure_claude_md_symlink(tmp_path)
    claude = tmp_path / "CLAUDE.md"
    assert claude.is_symlink()
    assert claude.read_text() == "policy"


def test_ensure_claude_md_symlink_noop_when_no_agents(tmp_path: Path) -> None:
    runner_app._ensure_claude_md_symlink(tmp_path)
    assert not (tmp_path / "CLAUDE.md").exists()


def test_ensure_claude_md_symlink_leaves_existing_real_file(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("policy")
    claude = tmp_path / "CLAUDE.md"
    claude.write_text("custom")
    runner_app._ensure_claude_md_symlink(tmp_path)
    assert not claude.is_symlink()
    assert claude.read_text() == "custom"


def test_ensure_claude_md_symlink_replaces_stale_symlink(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("policy")
    claude = tmp_path / "CLAUDE.md"
    # Point at a non-existent target (stale).
    import os
    os.symlink("stale_target", claude)
    runner_app._ensure_claude_md_symlink(tmp_path)
    assert claude.is_symlink()
    assert claude.read_text() == "policy"


def test_ensure_claude_md_symlink_idempotent(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("policy")
    runner_app._ensure_claude_md_symlink(tmp_path)
    runner_app._ensure_claude_md_symlink(tmp_path)
    assert (tmp_path / "CLAUDE.md").is_symlink()


def test_ensure_template_agents_file_also_creates_claude_md(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents_template = tmp_path / "template" / "AGENTS.md"
    agents_template.parent.mkdir()
    agents_template.write_text("template policy")
    repo = tmp_path / "repo"
    repo.mkdir()

    monkeypatch.setattr(
        runner_app,
        "_resolve_template_agents_path",
        lambda _src: agents_template,
    )
    runner_app._ensure_template_agents_file(tmp_path / "template", repo)

    assert (repo / "AGENTS.md").read_text() == "template policy"
    claude = repo / "CLAUDE.md"
    assert claude.is_symlink() or claude.is_file()
    assert claude.read_text() == "template policy"
