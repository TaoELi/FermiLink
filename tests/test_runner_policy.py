from __future__ import annotations

import os
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


@pytest.mark.parametrize(
    ("provider", "present_alias", "absent_alias"),
    [
        ("claude", "CLAUDE.md", "GEMINI.md"),
        ("gemini", "GEMINI.md", "CLAUDE.md"),
    ],
)
def test_ensure_template_agents_file_creates_only_active_provider_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    present_alias: str,
    absent_alias: str,
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
    monkeypatch.setattr(
        runner_app,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider=provider,
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    runner_app._ensure_template_agents_file(tmp_path / "template", repo)

    assert (repo / "AGENTS.md").read_text() == "template policy"
    alias = repo / present_alias
    assert alias.is_symlink() or alias.is_file()
    assert alias.read_text() == "template policy"
    assert not (repo / absent_alias).exists()


def test_ensure_template_agents_file_skips_provider_alias_for_codex(
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
    monkeypatch.setattr(
        runner_app,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    runner_app._ensure_template_agents_file(tmp_path / "template", repo)

    assert (repo / "AGENTS.md").read_text() == "template policy"
    assert not (repo / "CLAUDE.md").exists()
    assert not (repo / "GEMINI.md").exists()


def test_ensure_template_agents_file_removes_inactive_provider_alias_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents_template = tmp_path / "template" / "AGENTS.md"
    agents_template.parent.mkdir()
    agents_template.write_text("template policy")
    repo = tmp_path / "repo"
    repo.mkdir()
    os.symlink("AGENTS.md", repo / "GEMINI.md")

    monkeypatch.setattr(
        runner_app,
        "_resolve_template_agents_path",
        lambda _src: agents_template,
    )
    monkeypatch.setattr(
        runner_app,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="claude",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    runner_app._ensure_template_agents_file(tmp_path / "template", repo)

    assert (repo / "CLAUDE.md").exists()
    assert not (repo / "GEMINI.md").exists()


def test_ensure_template_agents_file_leaves_inactive_real_alias_file_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents_template = tmp_path / "template" / "AGENTS.md"
    agents_template.parent.mkdir()
    agents_template.write_text("template policy")
    repo = tmp_path / "repo"
    repo.mkdir()
    inactive_alias = repo / "GEMINI.md"
    inactive_alias.write_text("custom gemini policy")

    monkeypatch.setattr(
        runner_app,
        "_resolve_template_agents_path",
        lambda _src: agents_template,
    )
    monkeypatch.setattr(
        runner_app,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="claude",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    runner_app._ensure_template_agents_file(tmp_path / "template", repo)

    assert (repo / "CLAUDE.md").exists()
    assert inactive_alias.read_text() == "custom gemini policy"
