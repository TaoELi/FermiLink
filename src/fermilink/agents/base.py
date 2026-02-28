from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path

from fermilink.agent_runtime import DEFAULT_SANDBOX_POLICY


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

        raise NotImplementedError(
            f"Provider '{self.provider_id()}' does not implement build_exec_command()."
        )
