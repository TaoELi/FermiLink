from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from fermilink.config import resolve_fermilink_home


AGENT_RUNTIME_FILENAME = "agent_runtime.json"
AGENT_RUNTIME_VERSION = 1

DEFAULT_PROVIDER = "codex"
DEFAULT_SANDBOX_POLICY = "enforce"
DEFAULT_SANDBOX_MODE = "workspace-write"

SUPPORTED_PROVIDERS = ("codex", "claude", "gemini")
SUPPORTED_SANDBOX_POLICIES = ("enforce", "bypass")

ENV_PROVIDER = "FERMILINK_AGENT_PROVIDER"
ENV_SANDBOX_POLICY = "FERMILINK_AGENT_SANDBOX_POLICY"
ENV_SANDBOX_MODE = "FERMILINK_AGENT_SANDBOX_MODE"
ENV_MODEL = "FERMILINK_AGENT_MODEL"

_MODEL_UNSET: object = object()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_provider(raw: str | None) -> str:
    """
    Normalize and validate an agent provider name.

    Parameters
    ----------
    raw : str | None
        Raw value from user input or configuration.

    Returns
    -------
    str
        Normalized provider value.
    """
    value = (raw or "").strip().lower()
    if value not in SUPPORTED_PROVIDERS:
        valid = ", ".join(SUPPORTED_PROVIDERS)
        raise ValueError(f"Unsupported provider '{raw}'. Valid: {valid}")
    return value


def normalize_sandbox_policy(raw: str | None) -> str:
    """
    Normalize and validate an agent sandbox policy.

    Parameters
    ----------
    raw : str | None
        Raw value from user input or configuration.

    Returns
    -------
    str
        Normalized sandbox policy value.
    """
    value = (raw or "").strip().lower()
    if value not in SUPPORTED_SANDBOX_POLICIES:
        valid = ", ".join(SUPPORTED_SANDBOX_POLICIES)
        raise ValueError(f"Unsupported sandbox policy '{raw}'. Valid: {valid}")
    return value


def normalize_sandbox_mode(raw: str | None) -> str:
    """
    Normalize and validate an agent sandbox mode string.

    Parameters
    ----------
    raw : str | None
        Raw value from user input or configuration.

    Returns
    -------
    str
        Normalized sandbox mode value.
    """
    value = (raw or "").strip()
    if not value:
        raise ValueError("Sandbox mode cannot be empty.")
    return value


def normalize_model(raw: str | None) -> str | None:
    """
    Normalize an optional agent model override.

    Parameters
    ----------
    raw : str | None
        Raw model value from user input or configuration.

    Returns
    -------
    str | None
        Trimmed model id, or ``None`` when unset.
    """

    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    return value


@dataclass(frozen=True)
class AgentRuntimePolicy:
    provider: str = DEFAULT_PROVIDER
    sandbox_policy: str = DEFAULT_SANDBOX_POLICY
    sandbox_mode: str = DEFAULT_SANDBOX_MODE
    model: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "provider": self.provider,
            "sandbox_policy": self.sandbox_policy,
            "sandbox_mode": self.sandbox_mode,
            "model": self.model,
        }

    def as_env(self) -> dict[str, str]:
        env = {
            ENV_PROVIDER: self.provider,
            ENV_SANDBOX_POLICY: self.sandbox_policy,
            ENV_SANDBOX_MODE: self.sandbox_mode,
        }
        if isinstance(self.model, str) and self.model.strip():
            env[ENV_MODEL] = self.model
        return env


def _coerce_policy(
    *,
    provider: str,
    sandbox_policy: str,
    sandbox_mode: str,
    model: str | None,
) -> AgentRuntimePolicy:
    return AgentRuntimePolicy(
        provider=normalize_provider(provider),
        sandbox_policy=normalize_sandbox_policy(sandbox_policy),
        sandbox_mode=normalize_sandbox_mode(sandbox_mode),
        model=normalize_model(model),
    )


def resolve_agent_runtime_path() -> Path:
    """
    Return the path to the persisted agent runtime policy file.

    Returns
    -------
    Path
        Absolute path to the runtime policy file.
    """
    return resolve_fermilink_home() / AGENT_RUNTIME_FILENAME


def load_agent_runtime_policy(*, config_path: Path | None = None) -> AgentRuntimePolicy:
    """
    Load agent runtime policy settings from disk.

    Parameters
    ----------
    config_path : Path | None
        Optional explicit path to the runtime policy file.

    Returns
    -------
    AgentRuntimePolicy
        Loaded runtime policy, or defaults when file is missing/invalid.
    """
    path = config_path or resolve_agent_runtime_path()
    if not path.is_file():
        return AgentRuntimePolicy()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AgentRuntimePolicy()

    if not isinstance(payload, dict):
        return AgentRuntimePolicy()

    provider = payload.get("provider", DEFAULT_PROVIDER)
    sandbox_policy = payload.get("sandbox_policy", DEFAULT_SANDBOX_POLICY)
    sandbox_mode = payload.get("sandbox_mode", DEFAULT_SANDBOX_MODE)
    raw_model = payload.get("model", None)
    if raw_model is None:
        model: str | None = None
    elif isinstance(raw_model, str):
        model = raw_model
    else:
        model = str(raw_model)
    try:
        return _coerce_policy(
            provider=str(provider),
            sandbox_policy=str(sandbox_policy),
            sandbox_mode=str(sandbox_mode),
            model=model,
        )
    except ValueError:
        return AgentRuntimePolicy()


def resolve_agent_runtime_policy(
    *,
    provider: str | None = None,
    sandbox_policy: str | None = None,
    sandbox_mode: str | None = None,
    model: str | None | object = _MODEL_UNSET,
    env: Mapping[str, str] | None = None,
    config_path: Path | None = None,
) -> AgentRuntimePolicy:
    """
    Resolve the effective agent runtime policy with override precedence.

    Parameters
    ----------
    provider : str | None
        Provider identifier (for example `codex`, `claude`, or `gemini`).
    sandbox_policy : str | None
        Sandbox policy override (`enforce` or `bypass`).
    sandbox_mode : str | None
        Sandbox mode override passed to the provider runtime.
    model : str | None | object
        Optional model override. Use ``None`` to clear persisted model override,
        or leave unset to keep the current value.
    env : Mapping[str, str] | None
        Environment mapping used when resolving override variables.
    config_path : Path | None
        Optional explicit path to the runtime policy file.

    Returns
    -------
    AgentRuntimePolicy
        Effective runtime policy after applying precedence rules.
    """
    runtime = load_agent_runtime_policy(config_path=config_path)
    env_map = os.environ if env is None else env

    provider_value = runtime.provider
    env_provider = env_map.get(ENV_PROVIDER)
    if isinstance(env_provider, str) and env_provider.strip():
        provider_value = normalize_provider(env_provider)
    if provider is not None and provider.strip():
        provider_value = normalize_provider(provider)

    sandbox_policy_value = runtime.sandbox_policy
    env_sandbox_policy = env_map.get(ENV_SANDBOX_POLICY)
    if isinstance(env_sandbox_policy, str) and env_sandbox_policy.strip():
        sandbox_policy_value = normalize_sandbox_policy(env_sandbox_policy)
    if sandbox_policy is not None and sandbox_policy.strip():
        sandbox_policy_value = normalize_sandbox_policy(sandbox_policy)

    sandbox_mode_value = runtime.sandbox_mode
    env_sandbox_mode = env_map.get(ENV_SANDBOX_MODE)
    if isinstance(env_sandbox_mode, str) and env_sandbox_mode.strip():
        sandbox_mode_value = normalize_sandbox_mode(env_sandbox_mode)
    if sandbox_mode is not None and sandbox_mode.strip():
        sandbox_mode_value = normalize_sandbox_mode(sandbox_mode)

    model_value = runtime.model
    env_model = env_map.get(ENV_MODEL)
    if isinstance(env_model, str) and env_model.strip():
        model_value = normalize_model(env_model)
    if model is not _MODEL_UNSET:
        if model is None:
            model_value = None
        elif isinstance(model, str):
            model_value = normalize_model(model)
        else:
            raise ValueError("Model override must be a string or None.")

    return _coerce_policy(
        provider=provider_value,
        sandbox_policy=sandbox_policy_value,
        sandbox_mode=sandbox_mode_value,
        model=model_value,
    )


def save_agent_runtime_policy(
    *,
    provider: str | None = None,
    sandbox_policy: str | None = None,
    sandbox_mode: str | None = None,
    model: str | None | object = _MODEL_UNSET,
    config_path: Path | None = None,
) -> AgentRuntimePolicy:
    """
    Persist agent runtime policy settings and return the stored policy.

    Parameters
    ----------
    provider : str | None
        Provider identifier (for example `codex`, `claude`, or `gemini`).
    sandbox_policy : str | None
        Sandbox policy override (`enforce` or `bypass`).
    sandbox_mode : str | None
        Sandbox mode override passed to the provider runtime.
    model : str | None | object
        Optional model override. Use ``None`` to clear persisted model override,
        or leave unset to keep the current value.
    config_path : Path | None
        Optional explicit path to the runtime policy file.

    Returns
    -------
    AgentRuntimePolicy
        Persisted runtime policy object.
    """
    current = load_agent_runtime_policy(config_path=config_path)
    updated = resolve_agent_runtime_policy(
        provider=provider if provider is not None else current.provider,
        sandbox_policy=(
            sandbox_policy if sandbox_policy is not None else current.sandbox_policy
        ),
        sandbox_mode=sandbox_mode if sandbox_mode is not None else current.sandbox_mode,
        model=current.model if model is _MODEL_UNSET else model,
        env={},
        config_path=config_path,
    )

    payload: dict[str, Any] = {
        "version": AGENT_RUNTIME_VERSION,
        **updated.as_dict(),
        "updated_at": _now_iso(),
    }
    path = config_path or resolve_agent_runtime_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    tmp_path.replace(path)
    return updated
