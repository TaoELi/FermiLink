from __future__ import annotations

from pathlib import Path

from fermilink.agent_runtime import DEFAULT_SANDBOX_POLICY
from fermilink.agents.base import ProviderAgent


class CodexAgent(ProviderAgent):
    """Codex provider adapter with parity to the legacy command builder."""

    @property
    def provider(self) -> str:
        return "codex"

    @property
    def bin_env_key(self) -> str:
        return "FERMILINK_CODEX_BIN"

    @property
    def default_binary(self) -> str:
        return "codex"

    def resolve_binary(self, *, codex_bin: str | None = None) -> str:
        if isinstance(codex_bin, str) and codex_bin.strip():
            return codex_bin.strip()
        return super().resolve_binary(codex_bin=codex_bin)

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
        return self._build_codex_contract_command(
            provider_bin=provider_bin,
            repo_dir=repo_dir,
            prompt=prompt,
            sandbox_policy=sandbox_policy,
            sandbox_mode=sandbox_mode,
            model=model,
            reasoning_effort=reasoning_effort,
            json_output=json_output,
            reasoning_config_key="model_reasoning_effort",
        )
