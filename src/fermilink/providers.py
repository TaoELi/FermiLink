from __future__ import annotations

import os
from pathlib import Path

from fermilink.agent_runtime import (
    DEFAULT_PROVIDER,
    DEFAULT_SANDBOX_POLICY,
    normalize_provider,
    normalize_sandbox_policy,
)


PROVIDER_BIN_ENV = {
    "codex": "FERMILINK_CODEX_BIN",
    "claude": "FERMILINK_CLAUDE_BIN",
    "gemini": "FERMILINK_GEMINI_BIN",
}
PROVIDER_BIN_DEFAULT = {
    "codex": "codex",
    "claude": "claude",
    "gemini": "gemini",
}


def provider_bin_env_key(provider: str) -> str:
    normalized = normalize_provider(provider)
    return PROVIDER_BIN_ENV[normalized]


def resolve_provider_binary(
    provider: str,
    *,
    codex_bin: str | None = None,
) -> str:
    normalized = normalize_provider(provider)
    if normalized == "codex" and isinstance(codex_bin, str) and codex_bin.strip():
        return codex_bin.strip()

    env_key = PROVIDER_BIN_ENV[normalized]
    default_bin = PROVIDER_BIN_DEFAULT[normalized]
    raw = os.getenv(env_key, default_bin)
    cleaned = raw.strip() if isinstance(raw, str) else ""
    return cleaned or default_bin


def build_exec_command(
    *,
    provider: str = DEFAULT_PROVIDER,
    provider_bin: str,
    repo_dir: Path,
    prompt: str,
    sandbox_policy: str = DEFAULT_SANDBOX_POLICY,
    sandbox_mode: str | None = None,
    json_output: bool = True,
) -> list[str]:
    normalized_provider = normalize_provider(provider)
    normalized_policy = normalize_sandbox_policy(sandbox_policy)

    if normalized_provider != "codex":
        raise NotImplementedError(
            f"Provider '{normalized_provider}' is not implemented yet."
        )

    cmd = [provider_bin, "exec"]
    if json_output:
        cmd.append("--json")
    cmd.extend(["--cd", str(Path(repo_dir))])

    if normalized_policy == "bypass":
        cmd.append("--dangerously-bypass-approvals-and-sandbox")

    if (
        normalized_policy == "enforce"
        and isinstance(sandbox_mode, str)
        and sandbox_mode.strip()
    ):
        mode = sandbox_mode.strip()
        cmd.extend(["--sandbox", mode])
        if mode == "workspace-write":
            cmd.append("--full-auto")

    cmd.append(prompt)
    return cmd
