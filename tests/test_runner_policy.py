from __future__ import annotations

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
