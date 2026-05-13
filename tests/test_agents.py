from __future__ import annotations

import os
from pathlib import Path

import pytest

from fermilink.agents import (
    ClaudeAgent,
    CodexAgent,
    DeepseekAgent,
    GeminiAgent,
    get_default_agent_registry,
    get_provider_agent,
)
from fermilink.agents.base import ProviderAgent


def test_agent_registry_exposes_provider_binary_maps() -> None:
    registry = get_default_agent_registry()
    codex_default = "codex.cmd" if os.name == "nt" else "codex"
    assert registry.provider_bin_env_map() == {
        "codex": "FERMILINK_CODEX_BIN",
        "claude": "FERMILINK_CLAUDE_BIN",
        "gemini": "FERMILINK_GEMINI_BIN",
        "deepseek": "FERMILINK_DEEPSEEK_BIN",
    }
    assert registry.provider_bin_default_map() == {
        "codex": codex_default,
        "claude": "claude",
        "gemini": "gemini",
        "deepseek": "deepseek",
    }


def test_codex_agent_default_binary_is_windows_specific(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fermilink.agents.codex_agent as codex_agent

    monkeypatch.setattr(codex_agent.os, "name", "nt")
    assert CodexAgent().default_binary == "codex.cmd"

    monkeypatch.setattr(codex_agent.os, "name", "posix")
    assert CodexAgent().default_binary == "codex"


def test_codex_agent_resolve_binary_honors_explicit_override(monkeypatch) -> None:
    monkeypatch.setenv("FERMILINK_CODEX_BIN", "codex-env")
    agent = get_provider_agent("codex")
    assert agent.resolve_binary(provider_bin_override="codex-explicit") == (
        "codex-explicit"
    )
    assert agent.resolve_binary(provider_bin_override=None) == "codex-env"


def test_stub_provider_resolve_binary_uses_env(monkeypatch) -> None:
    monkeypatch.setenv("FERMILINK_GEMINI_BIN", "gemini-env")
    agent = get_provider_agent("gemini")
    assert agent.resolve_binary() == "gemini-env"


def test_deepseek_provider_resolve_binary_uses_env(monkeypatch) -> None:
    monkeypatch.setenv("FERMILINK_DEEPSEEK_BIN", "deepseek-env")
    agent = get_provider_agent("deepseek")
    assert agent.resolve_binary() == "deepseek-env"


def test_provider_base_build_exec_command_raises_clean_not_implemented(
    tmp_path: Path,
) -> None:
    class _TestAgent(ProviderAgent):
        @property
        def provider(self) -> str:
            return "test"

        @property
        def bin_env_key(self) -> str:
            return "FERMILINK_TEST_BIN"

        @property
        def default_binary(self) -> str:
            return "test-bin"

    with pytest.raises(NotImplementedError, match="does not implement"):
        _TestAgent().build_exec_command(
            provider_bin="test-bin",
            repo_dir=tmp_path,
            prompt="hello",
            sandbox_policy="enforce",
            sandbox_mode="read-only",
            model=None,
            reasoning_effort=None,
            json_output=True,
        )


def test_codex_agent_build_exec_command_matches_legacy(tmp_path: Path) -> None:
    agent = CodexAgent()
    cmd = agent.build_exec_command(
        provider_bin="codex",
        repo_dir=tmp_path,
        prompt="hello",
        sandbox_policy="enforce",
        sandbox_mode="workspace-write",
        model="gpt-5.3-codex",
        reasoning_effort="high",
        json_output=True,
    )
    assert cmd == [
        "codex",
        "exec",
        "--json",
        "--cd",
        str(Path(tmp_path)),
        "--sandbox",
        "workspace-write",
        "--full-auto",
        "--model",
        "gpt-5.3-codex",
        "--config",
        'model_reasoning_effort="high"',
        "hello",
    ]


def test_codex_agent_provider_runtime_hooks(tmp_path: Path) -> None:
    agent = CodexAgent()
    last_message_path = tmp_path / "last_message.txt"

    assert agent.uses_json_output_for_second_guess() is True
    assert agent.prepare_one_shot_exec_command(["codex", "exec", "hello"]) == [
        "codex",
        "exec",
        "--color",
        "always",
        "hello",
    ]
    assert agent.prepare_final_reply_capture_command(
        ["codex", "exec", "hello"],
        last_message_path=last_message_path,
        json_output=False,
    ) == [
        "codex",
        "exec",
        "--output-last-message",
        str(last_message_path),
        "hello",
    ]
    assert agent.prepare_final_reply_capture_command(
        ["codex", "exec", "hello"],
        last_message_path=last_message_path,
        json_output=True,
    ) == ["codex", "exec", "hello"]


def test_non_codex_agents_build_provider_native_commands(tmp_path: Path) -> None:
    claude = ClaudeAgent()
    assert claude.build_exec_command(
        provider_bin=claude.default_binary,
        repo_dir=tmp_path,
        prompt="hello",
        sandbox_policy="enforce",
        sandbox_mode="workspace-write",
        model="test-model",
        reasoning_effort="xhigh",
        json_output=True,
    ) == [
        "claude",
        "--print",
        "--add-dir",
        str(Path(tmp_path)),
        "--verbose",
        "--output-format",
        "stream-json",
        "--permission-mode",
        "acceptEdits",
        "--model",
        "test-model",
        "--effort",
        "high",
        "hello",
    ]

    gemini = GeminiAgent()
    assert gemini.build_exec_command(
        provider_bin=gemini.default_binary,
        repo_dir=tmp_path,
        prompt="hello",
        sandbox_policy="enforce",
        sandbox_mode="workspace-write",
        model="test-model",
        reasoning_effort="xhigh",
        json_output=True,
    ) == [
        "gemini",
        "--include-directories",
        str(Path(tmp_path)),
        "--output-format",
        "stream-json",
        "--sandbox",
        "--approval-mode",
        "auto_edit",
        "--model",
        "test-model",
        "--prompt=hello",
    ]

    deepseek = DeepseekAgent()
    assert deepseek.build_exec_command(
        provider_bin=deepseek.default_binary,
        repo_dir=tmp_path,
        prompt="hello",
        sandbox_policy="enforce",
        sandbox_mode="workspace-write",
        model="test-model",
        reasoning_effort="xhigh",
        json_output=True,
    ) == [
        "deepseek",
        "--workspace",
        str(Path(tmp_path)),
        "--quiet",
        "--no-global",
        "--model",
        "test-model",
        "--prompt=hello",
    ]
