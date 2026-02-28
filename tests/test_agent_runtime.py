from __future__ import annotations

from pathlib import Path

from fermilink.agent_runtime import (
    AgentRuntimePolicy,
    load_agent_runtime_policy,
    resolve_agent_runtime_policy,
    save_agent_runtime_policy,
)


def test_save_and_load_agent_runtime_policy(tmp_path: Path) -> None:
    config_path = tmp_path / "agent_runtime.json"

    saved = save_agent_runtime_policy(
        provider="claude",
        sandbox_policy="bypass",
        sandbox_mode="workspace-write",
        model="gpt-5.3-codex",
        reasoning_effort="high",
        config_path=config_path,
    )
    assert saved == AgentRuntimePolicy(
        provider="claude",
        sandbox_policy="bypass",
        sandbox_mode="workspace-write",
        model="gpt-5.3-codex",
        reasoning_effort="high",
    )

    loaded = load_agent_runtime_policy(config_path=config_path)
    assert loaded == saved


def test_resolve_policy_prefers_env_over_file(tmp_path: Path) -> None:
    config_path = tmp_path / "agent_runtime.json"
    save_agent_runtime_policy(
        provider="codex",
        sandbox_policy="enforce",
        sandbox_mode="workspace-write",
        config_path=config_path,
    )

    resolved = resolve_agent_runtime_policy(
        env={
            "FERMILINK_AGENT_PROVIDER": "gemini",
            "FERMILINK_AGENT_SANDBOX_POLICY": "bypass",
            "FERMILINK_AGENT_SANDBOX_MODE": "read-only",
            "FERMILINK_AGENT_MODEL": "gpt-5.2-medium",
            "FERMILINK_AGENT_REASONING_EFFORT": "xhigh",
        },
        config_path=config_path,
    )
    assert resolved.provider == "gemini"
    assert resolved.sandbox_policy == "bypass"
    assert resolved.sandbox_mode == "read-only"
    assert resolved.model == "gpt-5.2-medium"
    assert resolved.reasoning_effort == "xhigh"


def test_resolve_policy_prefers_function_overrides(tmp_path: Path) -> None:
    config_path = tmp_path / "agent_runtime.json"
    save_agent_runtime_policy(
        provider="codex",
        sandbox_policy="enforce",
        sandbox_mode="workspace-write",
        config_path=config_path,
    )

    resolved = resolve_agent_runtime_policy(
        provider="claude",
        sandbox_policy="bypass",
        sandbox_mode="workspace-write",
        model="gpt-5.3-codex",
        reasoning_effort="high",
        env={
            "FERMILINK_AGENT_PROVIDER": "gemini",
            "FERMILINK_AGENT_SANDBOX_POLICY": "enforce",
            "FERMILINK_AGENT_SANDBOX_MODE": "read-only",
            "FERMILINK_AGENT_MODEL": "gpt-5.2-medium",
            "FERMILINK_AGENT_REASONING_EFFORT": "low",
        },
        config_path=config_path,
    )
    assert resolved.provider == "claude"
    assert resolved.sandbox_policy == "bypass"
    assert resolved.sandbox_mode == "workspace-write"
    assert resolved.model == "gpt-5.3-codex"
    assert resolved.reasoning_effort == "high"


def test_save_policy_can_clear_model_override(tmp_path: Path) -> None:
    config_path = tmp_path / "agent_runtime.json"
    save_agent_runtime_policy(
        provider="codex",
        sandbox_policy="enforce",
        sandbox_mode="workspace-write",
        model="gpt-5.3-codex",
        reasoning_effort="xhigh",
        config_path=config_path,
    )

    updated = save_agent_runtime_policy(
        model=None,
        reasoning_effort=None,
        config_path=config_path,
    )
    assert updated.model is None
    assert updated.reasoning_effort is None
