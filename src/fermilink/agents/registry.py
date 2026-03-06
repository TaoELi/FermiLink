from __future__ import annotations

from collections.abc import Iterable

from fermilink.agent_runtime import normalize_provider
from fermilink.agents.base import ProviderAgent
from fermilink.agents.claude_agent import ClaudeAgent
from fermilink.agents.codex_agent import CodexAgent
from fermilink.agents.deepseek_agent import DeepseekAgent
from fermilink.agents.gemini_agent import GeminiAgent


class AgentRegistry:
    """Lookup registry for provider adapters."""

    def __init__(self, agents: Iterable[ProviderAgent]) -> None:
        mapping: dict[str, ProviderAgent] = {}
        for agent in agents:
            mapping[agent.provider_id()] = agent
        self._agents = mapping

    def get(self, provider: str) -> ProviderAgent:
        normalized = normalize_provider(provider)
        agent = self._agents.get(normalized)
        if agent is None:
            raise ValueError(f"No provider agent registered for '{normalized}'.")
        return agent

    def provider_bin_env_map(self) -> dict[str, str]:
        return {name: agent.bin_env_key for name, agent in self._agents.items()}

    def provider_bin_default_map(self) -> dict[str, str]:
        return {name: agent.default_binary for name, agent in self._agents.items()}

    def all(self) -> tuple[ProviderAgent, ...]:
        return tuple(self._agents.values())


_DEFAULT_AGENT_REGISTRY = AgentRegistry(
    agents=(
        CodexAgent(),
        ClaudeAgent(),
        GeminiAgent(),
        DeepseekAgent(),
    )
)


def get_default_agent_registry() -> AgentRegistry:
    return _DEFAULT_AGENT_REGISTRY


def get_provider_agent(provider: str) -> ProviderAgent:
    return _DEFAULT_AGENT_REGISTRY.get(provider)
