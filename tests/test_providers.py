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


def test_build_exec_command_provider_not_implemented(tmp_path: Path) -> None:
    with pytest.raises(NotImplementedError):
        build_exec_command(
            provider="gemini",
            provider_bin="gemini",
            repo_dir=tmp_path,
            prompt="hello",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
            json_output=True,
        )
