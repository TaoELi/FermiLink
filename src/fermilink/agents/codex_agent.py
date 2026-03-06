from __future__ import annotations

import os
from pathlib import Path

from fermilink.agent_runtime import (
    DEFAULT_SANDBOX_POLICY,
    normalize_reasoning_effort,
    normalize_sandbox_policy,
)
from fermilink.agents.base import ProviderAgent, insert_option_before_prompt


PLACEHOLDER_KEYS = {
    "YOUR_KEY_HERE",
    "YOUR_REAL_OPENAI_API_KEY",
    "YOUR_KEY*HERE",
    "CHANGEME",
}


def _ensure_dir_safe(path: Path) -> Path | None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return None


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
        override = self.resolve_binary_override(codex_bin)
        if isinstance(override, str):
            return override
        return super().resolve_binary(codex_bin=codex_bin)

    def resolve_binary_override(self, raw_override: str | None = None) -> str | None:
        if isinstance(raw_override, str) and raw_override.strip():
            return raw_override.strip()
        return None

    def supports_direct_terminal_stream(self) -> bool:
        return True

    def uses_json_output_for_second_guess(self) -> bool:
        return True

    def prepare_shared_turn_command(
        self,
        command: list[str],
        *,
        last_message_path: Path,
    ) -> list[str]:
        command = self.prepare_one_shot_exec_command(command)
        return self.prepare_final_reply_capture_command(
            command,
            last_message_path=last_message_path,
            json_output=False,
        )

    def prepare_one_shot_exec_command(self, command: list[str]) -> list[str]:
        return insert_option_before_prompt(command, "--color", "always")

    def prepare_final_reply_capture_command(
        self,
        command: list[str],
        *,
        last_message_path: Path,
        json_output: bool,
    ) -> list[str]:
        if json_output:
            return command
        return insert_option_before_prompt(
            command,
            "--output-last-message",
            str(last_message_path),
        )

    def sanitize_process_env(self, env: dict[str, str]) -> dict[str, str]:
        self._promote_prefixed_codex_env(env)
        auth_mode = (env.get("FERMILINK_CODEX_AUTH_MODE") or "").strip().lower()
        if auth_mode in {"login", "oauth", "keychain", "stored"}:
            env.pop("FERMILINK_CODEX_API_KEY", None)
            env.pop("FERMILINK_OPENAI_API_KEY", None)
            env.pop("CODEX_API_KEY", None)
            env.pop("OPENAI_API_KEY", None)
            return env

        key = env.get("FERMILINK_CODEX_API_KEY") or env.get(
            "FERMILINK_OPENAI_API_KEY"
        )
        if not key:
            key = env.get("CODEX_API_KEY") or env.get("OPENAI_API_KEY")
        if key and key.strip() in PLACEHOLDER_KEYS:
            env.pop("FERMILINK_CODEX_API_KEY", None)
            env.pop("FERMILINK_OPENAI_API_KEY", None)
            env.pop("CODEX_API_KEY", None)
            env.pop("OPENAI_API_KEY", None)
        return env

    def normalize_process_home(self, env: dict[str, str]) -> dict[str, str]:
        raw = env.get("FERMILINK_CODEX_HOME") or env.get("CODEX_HOME")
        if not raw:
            return env

        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path

        if _ensure_dir_safe(path):
            env["CODEX_HOME"] = str(path)
            env["FERMILINK_CODEX_HOME"] = str(path)
            return env

        home_fallback = Path.home() / ".codex"
        if _ensure_dir_safe(home_fallback):
            env["CODEX_HOME"] = str(home_fallback)
            env["FERMILINK_CODEX_HOME"] = str(home_fallback)
            return env

        local_fallback = Path.cwd() / ".codex"
        _ensure_dir_safe(local_fallback)
        env["CODEX_HOME"] = str(local_fallback)
        env["FERMILINK_CODEX_HOME"] = str(local_fallback)
        return env

    def supports_auto_compile_metadata_generation(self) -> bool:
        return True

    def service_env_overrides(self, *, cwd: Path) -> dict[str, str]:
        raw = os.getenv("FERMILINK_CODEX_HOME")
        if not isinstance(raw, str) or not raw.strip():
            return {}
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (cwd / path).resolve()
        return {"FERMILINK_CODEX_HOME": str(path)}

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

    @staticmethod
    def _promote_prefixed_codex_env(env: dict[str, str]) -> None:
        if env.get("FERMILINK_CODEX_AUTH_MODE") and not env.get("CODEX_AUTH_MODE"):
            env["CODEX_AUTH_MODE"] = str(env["FERMILINK_CODEX_AUTH_MODE"])
        if env.get("FERMILINK_CODEX_API_KEY") and not env.get("CODEX_API_KEY"):
            env["CODEX_API_KEY"] = str(env["FERMILINK_CODEX_API_KEY"])
        if env.get("FERMILINK_OPENAI_API_KEY") and not env.get("OPENAI_API_KEY"):
            env["OPENAI_API_KEY"] = str(env["FERMILINK_OPENAI_API_KEY"])
        if env.get("FERMILINK_CODEX_HOME") and not env.get("CODEX_HOME"):
            env["CODEX_HOME"] = str(env["FERMILINK_CODEX_HOME"])
