from __future__ import annotations

from pathlib import Path

from fermilink.agent_runtime import (
    DEFAULT_SANDBOX_POLICY,
    normalize_reasoning_effort,
    normalize_sandbox_policy,
)
from fermilink.agents.base import ProviderAgent


class ClaudeAgent(ProviderAgent):
    """Claude provider adapter with provider-native CLI translation."""

    REASONING_MAP = {"xhigh": "high"}
    SANDBOX_PERMISSION_MODE = {
        "read-only": "plan",
        "workspace-write": "acceptEdits",
    }

    @property
    def provider(self) -> str:
        return "claude"

    @property
    def bin_env_key(self) -> str:
        return "FERMILINK_CLAUDE_BIN"

    @property
    def default_binary(self) -> str:
        return "claude"

    def build_exec_command(
        self,
        *,
        provider_bin: str,
        repo_dir: Path,
        prompt: str,
        sandbox_policy: str = DEFAULT_SANDBOX_POLICY,
        sandbox_mode: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        json_output: bool = True,
    ) -> list[str]:
        normalized_policy = normalize_sandbox_policy(sandbox_policy)
        cmd = [provider_bin, "--print", "--add-dir", str(Path(repo_dir))]

        if json_output:
            # Claude requires --verbose when using --print + stream-json output.
            cmd.extend(["--verbose", "--output-format", "stream-json"])

        if normalized_policy == "bypass":
            cmd.extend(["--permission-mode", "bypassPermissions"])
        elif isinstance(sandbox_mode, str) and sandbox_mode.strip():
            permission_mode = self.SANDBOX_PERMISSION_MODE.get(sandbox_mode.strip())
            if isinstance(permission_mode, str):
                cmd.extend(["--permission-mode", permission_mode])

        if isinstance(model, str) and model.strip():
            cmd.extend(["--model", model.strip()])

        normalized_effort = normalize_reasoning_effort(reasoning_effort)
        if isinstance(normalized_effort, str) and normalized_effort:
            translated = self.REASONING_MAP.get(normalized_effort, normalized_effort)
            cmd.extend(["--effort", translated])

        cmd.append(prompt)
        return cmd
