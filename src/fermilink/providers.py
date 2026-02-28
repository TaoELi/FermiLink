from __future__ import annotations

from pathlib import Path

from fermilink.agent_runtime import (
    DEFAULT_PROVIDER,
    DEFAULT_SANDBOX_POLICY,
)
from fermilink.agents import get_default_agent_registry


_AGENT_REGISTRY = get_default_agent_registry()
PROVIDER_BIN_ENV = _AGENT_REGISTRY.provider_bin_env_map()
PROVIDER_BIN_DEFAULT = _AGENT_REGISTRY.provider_bin_default_map()


def provider_bin_env_key(provider: str) -> str:
    """
    Return the provider-specific environment variable used for binary overrides.

    Parameters
    ----------
    provider : str
        Provider identifier (for example `codex`, `claude`, `gemini`, or
        `deepseek`).

    Returns
    -------
    str
        Environment variable key for provider binary overrides.
    """
    return _AGENT_REGISTRY.get(provider).bin_env_key


def resolve_provider_binary(
    provider: str,
    *,
    codex_bin: str | None = None,
) -> str:
    """
    Resolve the executable name/path for the selected provider.

    Parameters
    ----------
    provider : str
        Provider identifier (for example `codex`, `claude`, `gemini`, or
        `deepseek`).
    codex_bin : str | None
        Optional override for the Codex executable when provider is `codex`.

    Returns
    -------
    str
        Resolved executable name/path for the provider.
    """
    return _AGENT_REGISTRY.get(provider).resolve_binary(codex_bin=codex_bin)


def build_exec_command(
    *,
    provider: str = DEFAULT_PROVIDER,
    provider_bin: str,
    repo_dir: Path,
    prompt: str,
    sandbox_policy: str = DEFAULT_SANDBOX_POLICY,
    sandbox_mode: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    json_output: bool = True,
) -> list[str]:
    """
    Build a provider-specific command for one exec/chat invocation.

    Parameters
    ----------
    provider : str
        Provider identifier (for example `codex`, `claude`, `gemini`, or
        `deepseek`).
    provider_bin : str
        Executable or command name used to run the provider.
    repo_dir : Path
        Workspace repository path receiving overlaid entries.
    prompt : str
        Prompt text sent to the provider process.
    sandbox_policy : str
        Sandbox policy override (`enforce` or `bypass`).
    sandbox_mode : str | None
        Sandbox mode override passed to the provider runtime.
    model : str | None
        Optional provider model override.
    reasoning_effort : str | None
        Optional provider reasoning-effort override (`low`, `medium`, `high`,
        or `xhigh`).
    json_output : bool
        Whether to request JSON output from the provider process.

    Returns
    -------
    list[str]
        Argument vector ready to execute via `subprocess`.
    """
    return _AGENT_REGISTRY.get(provider).build_exec_command(
        provider_bin=provider_bin,
        repo_dir=repo_dir,
        prompt=prompt,
        sandbox_policy=sandbox_policy,
        sandbox_mode=sandbox_mode,
        model=model,
        reasoning_effort=reasoning_effort,
        json_output=json_output,
    )
