from __future__ import annotations

from pathlib import Path

import pytest

from fermilink.providers import (
    build_exec_command,
    collect_provider_service_env_overrides,
    provider_supports_auto_compile_metadata_generation,
    resolve_provider_binary_override,
)


def test_build_exec_command_codex_enforced_workspace_write(tmp_path: Path) -> None:
    cmd = build_exec_command(
        provider="codex",
        provider_bin="codex",
        repo_dir=tmp_path,
        prompt="hello",
        sandbox_policy="enforce",
        sandbox_mode="workspace-write",
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
        "hello",
    ]


def test_build_exec_command_codex_bypass_sandbox(tmp_path: Path) -> None:
    cmd = build_exec_command(
        provider="codex",
        provider_bin="codex",
        repo_dir=tmp_path,
        prompt="hello",
        sandbox_policy="bypass",
        sandbox_mode="workspace-write",
        json_output=False,
    )
    assert cmd == [
        "codex",
        "exec",
        "--cd",
        str(Path(tmp_path)),
        "--dangerously-bypass-approvals-and-sandbox",
        "hello",
    ]


def test_build_exec_command_codex_with_model_override(tmp_path: Path) -> None:
    cmd = build_exec_command(
        provider="codex",
        provider_bin="codex",
        repo_dir=tmp_path,
        prompt="hello",
        sandbox_policy="enforce",
        sandbox_mode="read-only",
        model="gpt-5.3-codex",
        json_output=True,
    )
    assert cmd == [
        "codex",
        "exec",
        "--json",
        "--cd",
        str(Path(tmp_path)),
        "--sandbox",
        "read-only",
        "--model",
        "gpt-5.3-codex",
        "hello",
    ]


def test_build_exec_command_codex_with_reasoning_effort_override(
    tmp_path: Path,
) -> None:
    cmd = build_exec_command(
        provider="codex",
        provider_bin="codex",
        repo_dir=tmp_path,
        prompt="hello",
        sandbox_policy="enforce",
        sandbox_mode="read-only",
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
        "read-only",
        "--config",
        'model_reasoning_effort="high"',
        "hello",
    ]


def test_build_exec_command_gemini_maps_sandbox_modes(tmp_path: Path) -> None:
    cmd = build_exec_command(
        provider="gemini",
        provider_bin="gemini",
        repo_dir=tmp_path,
        prompt="hello",
        sandbox_policy="enforce",
        sandbox_mode="read-only",
        reasoning_effort="xhigh",
        json_output=True,
    )
    assert cmd == [
        "gemini",
        "--include-directories",
        str(Path(tmp_path)),
        "--output-format",
        "stream-json",
        "--sandbox",
        "--approval-mode",
        "plan",
        "--prompt=hello",
    ]


def test_build_exec_command_claude_bypass_sandbox(tmp_path: Path) -> None:
    cmd = build_exec_command(
        provider="claude",
        provider_bin="claude",
        repo_dir=tmp_path,
        prompt="hello",
        sandbox_policy="bypass",
        sandbox_mode="workspace-write",
        json_output=False,
    )
    assert cmd == [
        "claude",
        "--print",
        "--add-dir",
        str(Path(tmp_path)),
        "--permission-mode",
        "bypassPermissions",
        "hello",
    ]


def test_build_exec_command_opencode_model_and_reasoning(tmp_path: Path) -> None:
    cmd = build_exec_command(
        provider="opencode",
        provider_bin="opencode",
        repo_dir=tmp_path,
        prompt="hello",
        sandbox_policy="enforce",
        sandbox_mode="workspace-write",
        model="openai/gpt-5.1-codex",
        reasoning_effort="high",
        json_output=True,
    )
    assert cmd == [
        "opencode",
        "run",
        "--dir",
        str(Path(tmp_path)),
        "--format",
        "json",
        "--model",
        "openai/gpt-5.1-codex",
        "--variant",
        "high",
        "hello",
    ]


def test_build_exec_command_opencode_read_only_uses_plan_agent(
    tmp_path: Path,
) -> None:
    cmd = build_exec_command(
        provider="opencode",
        provider_bin="opencode",
        repo_dir=tmp_path,
        prompt="hello",
        sandbox_policy="enforce",
        sandbox_mode="read-only",
        json_output=False,
    )
    assert cmd == [
        "opencode",
        "run",
        "--dir",
        str(Path(tmp_path)),
        "--agent",
        "plan",
        "hello",
    ]


def test_build_exec_command_opencode_bypass_skips_permissions(
    tmp_path: Path,
) -> None:
    cmd = build_exec_command(
        provider="opencode",
        provider_bin="opencode",
        repo_dir=tmp_path,
        prompt="hello",
        sandbox_policy="bypass",
        sandbox_mode="workspace-write",
        json_output=False,
    )
    assert cmd == [
        "opencode",
        "run",
        "--dir",
        str(Path(tmp_path)),
        "--dangerously-skip-permissions",
        "hello",
    ]


def test_build_exec_command_rejects_unknown_provider(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_exec_command(
            provider="unknown",
            provider_bin="unknown",
            repo_dir=tmp_path,
            prompt="hello",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
            json_output=True,
        )


def test_resolve_provider_binary_override_only_applies_to_codex() -> None:
    assert (
        resolve_provider_binary_override("codex", raw_override="codex-explicit")
        == "codex-explicit"
    )
    assert (
        resolve_provider_binary_override("claude", raw_override="claude-explicit")
        is None
    )


def test_provider_metadata_generation_capability_is_agent_defined() -> None:
    assert provider_supports_auto_compile_metadata_generation("codex") is True
    assert provider_supports_auto_compile_metadata_generation("claude") is False
    assert provider_supports_auto_compile_metadata_generation("gemini") is False


def test_collect_provider_service_env_overrides_uses_agent_hooks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FERMILINK_CODEX_HOME", str(Path("relative-codex-home")))
    env = collect_provider_service_env_overrides(cwd=tmp_path)
    assert env == {
        "FERMILINK_CODEX_HOME": str((tmp_path / "relative-codex-home").resolve())
    }
