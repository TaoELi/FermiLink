from __future__ import annotations

from pathlib import Path

from fermilink.agent_runtime import (
    DEFAULT_SANDBOX_POLICY,
    normalize_reasoning_effort,
    normalize_sandbox_policy,
)
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

    def _build_codex_contract_command(
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
        bypass_flag: str = "--dangerously-bypass-approvals-and-sandbox",
        full_auto_flag: str | None = "--full-auto",
        reasoning_config_key: str = "model_reasoning_effort",
        reasoning_effort_map: dict[str, str] | None = None,
    ) -> list[str]:
        """Build a command using the Codex ``exec`` CLI contract."""

        normalized_policy = normalize_sandbox_policy(sandbox_policy)
        cmd = [provider_bin, "exec"]
        if json_output:
            cmd.append("--json")
        cmd.extend(["--cd", str(Path(repo_dir))])

        if normalized_policy == "bypass":
            cmd.append(bypass_flag)

        if (
            normalized_policy == "enforce"
            and isinstance(sandbox_mode, str)
            and sandbox_mode.strip()
        ):
            mode = sandbox_mode.strip()
            cmd.extend(["--sandbox", mode])
            if mode == "workspace-write" and isinstance(full_auto_flag, str):
                cmd.append(full_auto_flag)

        if isinstance(model, str) and model.strip():
            cmd.extend(["--model", model.strip()])

        normalized_effort = normalize_reasoning_effort(reasoning_effort)
        if isinstance(normalized_effort, str) and normalized_effort:
            translated_effort = (
                reasoning_effort_map.get(normalized_effort, normalized_effort)
                if isinstance(reasoning_effort_map, dict)
                else normalized_effort
            )
            cmd.extend(
                ["--config", f'{reasoning_config_key}="{translated_effort}"']
            )

        cmd.append(prompt)
        return cmd
