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
        config_path=config_path,
    )
    assert saved == AgentRuntimePolicy(
        provider="claude",
        sandbox_policy="bypass",
        sandbox_mode="workspace-write",
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
        },
        config_path=config_path,
    )
    assert resolved.provider == "gemini"
    assert resolved.sandbox_policy == "bypass"
    assert resolved.sandbox_mode == "read-only"


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
        env={
            "FERMILINK_AGENT_PROVIDER": "gemini",
            "FERMILINK_AGENT_SANDBOX_POLICY": "enforce",
            "FERMILINK_AGENT_SANDBOX_MODE": "read-only",
        },
        config_path=config_path,
    )
    assert resolved.provider == "claude"
    assert resolved.sandbox_policy == "bypass"
    assert resolved.sandbox_mode == "workspace-write"
