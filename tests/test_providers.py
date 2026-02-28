from __future__ import annotations

from pathlib import Path

import pytest

from fermilink.providers import build_exec_command


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


def test_build_exec_command_gemini_with_translated_reasoning(tmp_path: Path) -> None:
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
        "exec",
        "--cd",
        str(Path(tmp_path)),
        "--dangerously-bypass-approvals-and-sandbox",
        "hello",
    ]


def test_build_exec_command_deepseek_model_and_reasoning(tmp_path: Path) -> None:
    cmd = build_exec_command(
        provider="deepseek",
        provider_bin="deepseek",
        repo_dir=tmp_path,
        prompt="hello",
        sandbox_policy="enforce",
        sandbox_mode="workspace-write",
        model="deepseek-chat",
        reasoning_effort="high",
        json_output=True,
    )
    assert cmd == [
        "deepseek",
        "exec",
        "--json",
        "--cd",
        str(Path(tmp_path)),
        "--sandbox",
        "workspace-write",
        "--full-auto",
        "--model",
        "deepseek-chat",
        "--config",
        'model_reasoning_effort="high"',
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
