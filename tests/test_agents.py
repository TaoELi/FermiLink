from __future__ import annotations

from pathlib import Path

from fermilink.agents import (
    ClaudeAgent,
    CodexAgent,
    DeepseekAgent,
    GeminiAgent,
    get_default_agent_registry,
    get_provider_agent,
)


def test_agent_registry_exposes_provider_binary_maps() -> None:
    registry = get_default_agent_registry()
    assert registry.provider_bin_env_map() == {
        "codex": "FERMILINK_CODEX_BIN",
        "claude": "FERMILINK_CLAUDE_BIN",
        "gemini": "FERMILINK_GEMINI_BIN",
        "deepseek": "FERMILINK_DEEPSEEK_BIN",
    }
    assert registry.provider_bin_default_map() == {
        "codex": "codex",
        "claude": "claude",
        "gemini": "gemini",
        "deepseek": "deepseek",
    }


def test_codex_agent_resolve_binary_honors_explicit_override(monkeypatch) -> None:
    monkeypatch.setenv("FERMILINK_CODEX_BIN", "codex-env")
    agent = get_provider_agent("codex")
    assert agent.resolve_binary(codex_bin="codex-explicit") == "codex-explicit"
    assert agent.resolve_binary(codex_bin=None) == "codex-env"


def test_stub_provider_resolve_binary_uses_env(monkeypatch) -> None:
    monkeypatch.setenv("FERMILINK_GEMINI_BIN", "gemini-env")
    agent = get_provider_agent("gemini")
    assert agent.resolve_binary() == "gemini-env"


def test_deepseek_provider_resolve_binary_uses_env(monkeypatch) -> None:
    monkeypatch.setenv("FERMILINK_DEEPSEEK_BIN", "deepseek-env")
    agent = get_provider_agent("deepseek")
    assert agent.resolve_binary() == "deepseek-env"


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


def test_non_codex_agent_build_exec_command_supported(tmp_path: Path) -> None:
    for agent in (ClaudeAgent(), GeminiAgent(), DeepseekAgent()):
        cmd = agent.build_exec_command(
            provider_bin=agent.default_binary,
            repo_dir=tmp_path,
            prompt="hello",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
            model="test-model",
            reasoning_effort="xhigh",
            json_output=True,
        )
        assert cmd == [
            agent.default_binary,
            "exec",
            "--json",
            "--cd",
            str(Path(tmp_path)),
            "--sandbox",
            "workspace-write",
            "--full-auto",
            "--model",
            "test-model",
            "--config",
            'model_reasoning_effort="high"',
            "hello",
        ]
