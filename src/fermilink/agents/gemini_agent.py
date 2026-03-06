from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fermilink.agent_runtime import (
    DEFAULT_SANDBOX_POLICY,
    normalize_reasoning_effort,
    normalize_sandbox_policy,
)
from fermilink.agents.base import ProviderAgent


_GEMINI_DEFAULT_MODEL = "auto-gemini-3"
_GEMINI_REASONING_THINKING_LEVEL = {
    "low": "LOW",
    "medium": "MEDIUM",
    "high": "HIGH",
    "xhigh": "HIGH",
}
_GEMINI_REASONING_THINKING_BUDGET = {
    "low": 1024,
    "medium": 4096,
    "high": 8192,
    "xhigh": 16384,
}


def _is_gemini_thinking_level_model(model_name: str) -> bool:
    normalized = model_name.strip().lower()
    return normalized.startswith("gemini-3") or normalized.startswith("auto-gemini-3")


def _build_gemini_reasoning_settings_payload(
    *,
    target_model: str,
    reasoning_effort: str,
) -> dict[str, object]:
    thinking_config: dict[str, object] = {"includeThoughts": True}
    if _is_gemini_thinking_level_model(target_model):
        thinking_level = _GEMINI_REASONING_THINKING_LEVEL.get(reasoning_effort, "HIGH")
        thinking_config["thinkingLevel"] = thinking_level
    else:
        thinking_budget = _GEMINI_REASONING_THINKING_BUDGET.get(
            reasoning_effort, 8192
        )
        thinking_config["thinkingBudget"] = int(thinking_budget)

    return {
        "modelConfigs": {
            "customOverrides": [
                {
                    "match": {"model": target_model},
                    "modelConfig": {
                        "generateContentConfig": {
                            "thinkingConfig": thinking_config,
                        }
                    },
                }
            ]
        }
    }


class GeminiAgent(ProviderAgent):
    """Gemini provider adapter with provider-native CLI translation."""

    @property
    def provider(self) -> str:
        return "gemini"

    @property
    def bin_env_key(self) -> str:
        return "FERMILINK_GEMINI_BIN"

    @property
    def default_binary(self) -> str:
        return "gemini"

    def uses_json_stream(self) -> bool:
        return True

    def workspace_instruction_alias_name(self) -> str | None:
        return "GEMINI.md"

    def prepare_runtime_env(
        self,
        env: dict[str, str],
        *,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> tuple[dict[str, str], list[Path]]:
        normalized_effort = normalize_reasoning_effort(reasoning_effort)
        if not isinstance(normalized_effort, str) or not normalized_effort:
            return env, []

        target_model = (
            model.strip()
            if isinstance(model, str) and model.strip()
            else _GEMINI_DEFAULT_MODEL
        )
        payload = _build_gemini_reasoning_settings_payload(
            target_model=target_model,
            reasoning_effort=normalized_effort,
        )
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                prefix="fermilink-gemini-settings-",
                suffix=".json",
                encoding="utf-8",
                delete=False,
            ) as handle:
                handle.write(json.dumps(payload, ensure_ascii=True, indent=2))
                handle.write("\n")
                settings_path = Path(handle.name)
        except OSError as exc:
            raise RuntimeError(
                "Failed to prepare Gemini reasoning-effort settings override."
            ) from exc

        updated = dict(env)
        updated["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = str(settings_path)
        return updated, [settings_path]

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
        del reasoning_effort
        normalized_policy = normalize_sandbox_policy(sandbox_policy)
        cmd = [provider_bin, "--include-directories", str(Path(repo_dir))]

        if json_output:
            cmd.extend(["--output-format", "stream-json"])

        if normalized_policy == "bypass":
            cmd.extend(["--approval-mode", "yolo"])
        else:
            cmd.append("--sandbox")
            if isinstance(sandbox_mode, str) and sandbox_mode.strip():
                mode = sandbox_mode.strip()
                if mode == "read-only":
                    cmd.extend(["--approval-mode", "plan"])
                elif mode == "workspace-write":
                    cmd.extend(["--approval-mode", "auto_edit"])

        if isinstance(model, str) and model.strip():
            cmd.extend(["--model", model.strip()])

        cmd.append(f"--prompt={prompt}")
        return cmd
