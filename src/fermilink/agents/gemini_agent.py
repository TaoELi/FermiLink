from __future__ import annotations

from pathlib import Path

from fermilink.agent_runtime import DEFAULT_SANDBOX_POLICY, normalize_sandbox_policy
from fermilink.agents.base import ProviderAgent


class GeminiAgent(ProviderAgent):
    """Gemini provider adapter with provider-native CLI translation."""

    @property
    def provider(self) -> str:
        return "gemini"

    @property
    def bin_env_key(self) -> str:
        return "FERMILINK_GEMINI_BIN"

    @property
    def default_binary(self) -> str:
        return "gemini"

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
        cmd = [provider_bin, "--include-directories", str(Path(repo_dir))]

        if json_output:
            cmd.extend(["--output-format", "stream-json"])

        if normalized_policy == "bypass":
            cmd.extend(["--approval-mode", "yolo"])
        else:
            cmd.append("--sandbox")
            if isinstance(sandbox_mode, str) and sandbox_mode.strip():
                mode = sandbox_mode.strip()
                if mode == "read-only":
                    cmd.extend(["--approval-mode", "plan"])
                elif mode == "workspace-write":
                    cmd.extend(["--approval-mode", "auto_edit"])

        if isinstance(model, str) and model.strip():
            cmd.extend(["--model", model.strip()])

        cmd.append(f"--prompt={prompt}")
        return cmd
