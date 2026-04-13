"""Provider runtime wrapper for design-mode turns."""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from fermilink.agents import get_provider_agent
from fermilink.agent_runtime import AgentRuntimePolicy


DESIGN_TEMP_AGENTS_MARKER = "<!-- FERMILINK_TEMP_DESIGN_AGENTS -->"
DESIGN_TEMP_AGENTS_HEADER = f"{DESIGN_TEMP_AGENTS_MARKER}\n"
_WORKSPACE_INSTRUCTION_ALIAS_PROVIDERS = ("claude", "gemini")


def _temporary_design_agents_content(content: str) -> str:
    text = str(content or "")
    if text.startswith(DESIGN_TEMP_AGENTS_HEADER):
        return text
    return f"{DESIGN_TEMP_AGENTS_HEADER}{text}"


@contextmanager
def temporary_design_agents(
    repo_dir: Path,
    *,
    provider: str,
    content: str,
):
    repo_agents = repo_dir / "AGENTS.md"
    original_agents_exists = repo_agents.exists()
    original_agents_text = ""
    if original_agents_exists and repo_agents.is_file():
        try:
            original_agents_text = repo_agents.read_text(encoding="utf-8")
        except OSError:
            original_agents_text = ""

    provider_candidates = list(_WORKSPACE_INSTRUCTION_ALIAS_PROVIDERS)
    provider_name = str(provider or "").strip()
    if provider_name and provider_name not in provider_candidates:
        provider_candidates.append(provider_name)

    alias_states: dict[str, tuple[bool, bool, str]] = {}
    alias_names: set[str] = set()
    for candidate in provider_candidates:
        alias_name = get_provider_agent(candidate).workspace_instruction_alias_name()
        if isinstance(alias_name, str) and alias_name.strip():
            alias_names.add(alias_name.strip())

    for alias_name in sorted(alias_names):
        alias_path = repo_dir / alias_name
        if alias_path.exists() or alias_path.is_symlink():
            if alias_path.is_symlink():
                try:
                    alias_states[alias_name] = (True, True, os.readlink(alias_path))
                except OSError:
                    alias_states[alias_name] = (True, True, "")
            else:
                try:
                    alias_states[alias_name] = (
                        True,
                        False,
                        alias_path.read_text(encoding="utf-8"),
                    )
                except OSError:
                    alias_states[alias_name] = (True, False, "")
        else:
            alias_states[alias_name] = (False, False, "")

    repo_agents.write_text(
        _temporary_design_agents_content(content),
        encoding="utf-8",
    )
    for candidate in provider_candidates:
        get_provider_agent(candidate).ensure_workspace_instruction_alias(repo_dir)
    try:
        yield
    finally:
        if original_agents_exists:
            repo_agents.write_text(original_agents_text, encoding="utf-8")
        else:
            try:
                repo_agents.unlink(missing_ok=True)
            except OSError:
                pass

        for alias_name, alias_state in alias_states.items():
            alias_path = repo_dir / alias_name
            try:
                alias_path.unlink(missing_ok=True)
            except OSError:
                pass
            existed, was_symlink, stored = alias_state
            if not existed:
                continue
            try:
                if was_symlink:
                    os.symlink(stored, alias_path)
                else:
                    alias_path.write_text(stored, encoding="utf-8")
            except OSError:
                continue


@dataclass(frozen=True)
class DesignTurnResult:
    assistant_text: str
    return_code: int
    stderr: str
    stopped_by_user: bool

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "DesignTurnResult":
        return cls(
            assistant_text=str(payload.get("assistant_text") or ""),
            return_code=int(payload.get("return_code") or 0),
            stderr=str(payload.get("stderr") or ""),
            stopped_by_user=bool(payload.get("stopped_by_user")),
        )


def run_design_turn(
    *,
    repo_dir: Path,
    prompt: str,
    policy: AgentRuntimePolicy,
    instruction_text: str,
    provider_bin_override: str | None = None,
) -> DesignTurnResult:
    from fermilink import cli

    with temporary_design_agents(
        repo_dir,
        provider=policy.provider,
        content=instruction_text,
    ):
        payload = cli._run_exec_chat_turn(
            repo_dir=repo_dir,
            prompt=prompt,
            sandbox=policy.sandbox_mode,
            provider_bin_override=provider_bin_override,
            provider=policy.provider,
            sandbox_policy=policy.sandbox_policy,
            model=policy.model,
            reasoning_effort=policy.reasoning_effort,
        )
    return DesignTurnResult.from_payload(payload)
