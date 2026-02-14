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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_provider(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    if value not in SUPPORTED_PROVIDERS:
        valid = ", ".join(SUPPORTED_PROVIDERS)
        raise ValueError(f"Unsupported provider '{raw}'. Valid: {valid}")
    return value


def normalize_sandbox_policy(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    if value not in SUPPORTED_SANDBOX_POLICIES:
        valid = ", ".join(SUPPORTED_SANDBOX_POLICIES)
        raise ValueError(f"Unsupported sandbox policy '{raw}'. Valid: {valid}")
    return value


def normalize_sandbox_mode(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        raise ValueError("Sandbox mode cannot be empty.")
    return value


@dataclass(frozen=True)
class AgentRuntimePolicy:
    provider: str = DEFAULT_PROVIDER
    sandbox_policy: str = DEFAULT_SANDBOX_POLICY
    sandbox_mode: str = DEFAULT_SANDBOX_MODE

    def as_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "sandbox_policy": self.sandbox_policy,
            "sandbox_mode": self.sandbox_mode,
        }

    def as_env(self) -> dict[str, str]:
        return {
            ENV_PROVIDER: self.provider,
            ENV_SANDBOX_POLICY: self.sandbox_policy,
            ENV_SANDBOX_MODE: self.sandbox_mode,
        }


def _coerce_policy(
    *,
    provider: str,
    sandbox_policy: str,
    sandbox_mode: str,
) -> AgentRuntimePolicy:
    return AgentRuntimePolicy(
        provider=normalize_provider(provider),
        sandbox_policy=normalize_sandbox_policy(sandbox_policy),
        sandbox_mode=normalize_sandbox_mode(sandbox_mode),
    )


def resolve_agent_runtime_path() -> Path:
    return resolve_fermilink_home() / AGENT_RUNTIME_FILENAME


def load_agent_runtime_policy(*, config_path: Path | None = None) -> AgentRuntimePolicy:
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
    try:
        return _coerce_policy(
            provider=str(provider),
            sandbox_policy=str(sandbox_policy),
            sandbox_mode=str(sandbox_mode),
        )
    except ValueError:
        return AgentRuntimePolicy()


def resolve_agent_runtime_policy(
    *,
    provider: str | None = None,
    sandbox_policy: str | None = None,
    sandbox_mode: str | None = None,
    env: Mapping[str, str] | None = None,
    config_path: Path | None = None,
) -> AgentRuntimePolicy:
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

    return _coerce_policy(
        provider=provider_value,
        sandbox_policy=sandbox_policy_value,
        sandbox_mode=sandbox_mode_value,
    )


def save_agent_runtime_policy(
    *,
    provider: str | None = None,
    sandbox_policy: str | None = None,
    sandbox_mode: str | None = None,
    config_path: Path | None = None,
) -> AgentRuntimePolicy:
    current = load_agent_runtime_policy(config_path=config_path)
    updated = resolve_agent_runtime_policy(
        provider=provider if provider is not None else current.provider,
        sandbox_policy=(
            sandbox_policy if sandbox_policy is not None else current.sandbox_policy
        ),
        sandbox_mode=sandbox_mode if sandbox_mode is not None else current.sandbox_mode,
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
