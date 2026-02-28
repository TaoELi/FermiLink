from __future__ import annotations

from pathlib import Path

from fermilink.agent_runtime import DEFAULT_SANDBOX_POLICY
from fermilink.agents.base import ProviderAgent


class GeminiAgent(ProviderAgent):
    """Gemini provider adapter with codex-intent policy translation."""

    REASONING_MAP = {"xhigh": "high"}

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
            reasoning_effort_map=self.REASONING_MAP,
        )
