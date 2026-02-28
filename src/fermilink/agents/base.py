from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path

from fermilink.agent_runtime import (
    DEFAULT_SANDBOX_POLICY,
    normalize_reasoning_effort,
    normalize_sandbox_policy,
)


class ProviderAgent(ABC):
    """Base provider contract for binary resolution and command assembly."""

    @property
    @abstractmethod
    def provider(self) -> str:
        """Return canonical provider id (for example ``codex``)."""

    @property
    @abstractmethod
    def bin_env_key(self) -> str:
        """Return environment key used for binary override lookup."""

    @property
    @abstractmethod
    def default_binary(self) -> str:
        """Return provider CLI default binary name."""

    def provider_id(self) -> str:
        return self.provider

    def resolve_binary(self, *, codex_bin: str | None = None) -> str:
        """Resolve executable name/path for this provider."""

        del codex_bin
        raw = os.getenv(self.bin_env_key, self.default_binary)
        cleaned = raw.strip() if isinstance(raw, str) else ""
        return cleaned or self.default_binary

    @abstractmethod
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
        """Build provider-specific argv for one exec/chat invocation."""

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
        """
        Build a command using the shared codex-style ``exec`` CLI contract.

        This keeps cross-provider integration changes minimal while allowing
        provider-specific option translation via ``reasoning_effort_map`` and
        ``reasoning_config_key``.
        """

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
