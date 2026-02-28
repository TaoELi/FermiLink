from __future__ import annotations

from pathlib import Path

from fermilink.agent_runtime import DEFAULT_SANDBOX_POLICY, normalize_sandbox_policy
from fermilink.agents.base import ProviderAgent


class DeepseekAgent(ProviderAgent):
    """DeepSeek provider adapter with provider-native CLI translation."""

    @property
    def provider(self) -> str:
        return "deepseek"

    @property
    def bin_env_key(self) -> str:
        return "FERMILINK_DEEPSEEK_BIN"

    @property
    def default_binary(self) -> str:
        return "deepseek"

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
        del reasoning_effort
        normalized_policy = normalize_sandbox_policy(sandbox_policy)
        cmd = [provider_bin, "--workspace", str(Path(repo_dir))]

        if json_output:
            cmd.append("--quiet")

        if normalized_policy == "bypass":
            cmd.append("--global")
        else:
            cmd.append("--no-global")
            if isinstance(sandbox_mode, str) and sandbox_mode.strip() == "read-only":
                cmd.append("--read-only")

        if isinstance(model, str) and model.strip():
            cmd.extend(["--model", model.strip()])

        cmd.append(f"--prompt={prompt}")
        return cmd
