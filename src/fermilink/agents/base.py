from __future__ import annotations

import json
import os
import re
import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from fermilink.agent_runtime import DEFAULT_SANDBOX_POLICY


# ANSI color palette for provider stream rendering.
_ANSI = {
    "reset": "\033[0m",
    "agent_label": "\033[1;35m",
    "thinking": "\033[1;35m",
    "tool_label": "\033[1;32m",
    "tool_cmd": "\033[36m",
    "tool_out": "\033[90m",
    "text": "\033[0m",
}
_ANSI_OFF = {key: "" for key in _ANSI}
_SYSTEM_BLOCK_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_TOOL_OUTPUT_MAX_LINES = 5


def insert_option_before_prompt(command: list[str], *option_tokens: str) -> list[str]:
    """Insert option tokens before the final prompt argument."""

    if not command:
        return command
    prompt_arg = command[-1]
    return [*command[:-1], *option_tokens, prompt_arg]


def _truncate_tool_output(text: str, max_lines: int = _TOOL_OUTPUT_MAX_LINES) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    omitted = len(lines) - max_lines
    return "\n".join(lines[:max_lines]) + f"\n… ({omitted} more lines)"


def _strip_thinking_noise(text: str) -> str:
    cleaned = _SYSTEM_BLOCK_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _extract_text_like(value: object, *, strip: bool = True) -> str:
    if isinstance(value, str):
        return value.strip() if strip else value
    if isinstance(value, list):
        parts = [_extract_text_like(item, strip=strip) for item in value]
        return "".join(part for part in parts if part)
    if isinstance(value, dict):
        parts: list[str] = []
        for key in (
            "text",
            "content",
            "message",
            "raw_content",
            "summary_text",
            "summary",
            "description",
            "value",
            "output",
            "stdout",
            "stderr",
            "delta",
            "patch",
            "diff",
        ):
            nested = _extract_text_like(value.get(key), strip=strip)
            if nested:
                parts.append(nested)
        if parts:
            return "".join(parts)
    return ""


def _event_item(event: dict) -> dict:
    item = event.get("item")
    return item if isinstance(item, dict) else {}


def _event_item_type(event: dict) -> str:
    item_type = _event_item(event).get("type")
    if isinstance(item_type, str) and item_type.strip():
        return item_type.strip()
    event_type = event.get("type")
    if isinstance(event_type, str) and event_type.strip():
        return event_type.strip()
    return ""


def _format_tool_input_preview(payload: object) -> str:
    if isinstance(payload, dict):
        candidate = (
            payload.get("command")
            or payload.get("cmd")
            or payload.get("parsed_cmd")
            or payload.get("shell_command")
            or payload.get("file_path")
            or payload.get("path")
        )
        if isinstance(candidate, str) and candidate:
            return candidate
        return json.dumps(payload, ensure_ascii=False)
    if payload is None:
        return ""
    return str(payload)


def _extract_command_preview(event: dict) -> str:
    def from_mapping(mapping: dict) -> str:
        for key in (
            "command",
            "cmd",
            "parsed_cmd",
            "shell_command",
            "action",
            "path",
            "file_path",
        ):
            value = mapping.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for key in ("arguments", "parameters", "input"):
            value = mapping.get(key)
            if isinstance(value, dict):
                preview = _format_tool_input_preview(value)
                if preview:
                    return preview
            if isinstance(value, str) and value.strip():
                try:
                    parsed = json.loads(value)
                except (json.JSONDecodeError, TypeError, ValueError):
                    return value.strip()
                if isinstance(parsed, dict):
                    preview = _format_tool_input_preview(parsed)
                    if preview:
                        return preview
                return value.strip()
        return ""

    item = _event_item(event)
    return from_mapping(item) or from_mapping(event)


def _extract_output_preview(event: dict, item: dict | None = None) -> str:
    output_keys = (
        "output",
        "stdout",
        "stderr",
        "aggregated_output",
        "combined_output",
        "formatted_output",
        "result",
        "content",
        "message",
        "text",
        "delta",
    )

    def from_mapping(mapping: dict) -> str:
        parts: list[str] = []
        for key in output_keys:
            if key not in mapping:
                continue
            text = _extract_text_like(mapping.get(key), strip=True)
            if text:
                parts.append(text)
        return "\n".join(parts)

    item_mapping = item if isinstance(item, dict) else _event_item(event)
    output = from_mapping(event)
    item_output = from_mapping(item_mapping)
    if output and item_output and item_output not in output:
        return f"{output}\n{item_output}"
    return output or item_output


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

    def resolve_binary(self, *, provider_bin_override: str | None = None) -> str:
        """Resolve executable name/path for this provider."""

        del provider_bin_override
        raw = os.getenv(self.bin_env_key, self.default_binary)
        cleaned = raw.strip() if isinstance(raw, str) else ""
        return cleaned or self.default_binary

    def resolve_binary_override(self, raw_override: str | None = None) -> str | None:
        """Resolve an optional compatibility override for this provider binary."""

        del raw_override
        return None

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

    def uses_json_stream(self) -> bool:
        """Return whether shared CLI execution should request stream-json output."""

        return False

    def uses_json_output_for_second_guess(self) -> bool:
        """Return whether second-guess subprocesses should request JSON output."""

        return False

    def supports_direct_terminal_stream(self) -> bool:
        """Return whether direct terminal passthrough is preferred for this provider."""

        return False

    def prepare_shared_turn_command(
        self,
        command: list[str],
        *,
        last_message_path: Path,
    ) -> list[str]:
        """Apply provider-specific command tweaks for shared chat/loop turns."""

        del last_message_path
        return command

    def prepare_one_shot_exec_command(self, command: list[str]) -> list[str]:
        """Apply provider-specific command tweaks for one-shot exec runs."""

        return command

    def prepare_final_reply_capture_command(
        self,
        command: list[str],
        *,
        last_message_path: Path,
        json_output: bool,
    ) -> list[str]:
        """Apply provider-specific command tweaks for final-reply file capture."""

        del last_message_path, json_output
        return command

    def sanitize_process_env(self, env: dict[str, str]) -> dict[str, str]:
        """Apply provider-specific subprocess environment sanitation."""

        return env

    def normalize_process_home(self, env: dict[str, str]) -> dict[str, str]:
        """Normalize any provider-specific writable home paths."""

        return env

    def prepare_runtime_env(
        self,
        env: dict[str, str],
        *,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> tuple[dict[str, str], list[Path]]:
        """Apply provider runtime env overrides and return cleanup paths."""

        del model, reasoning_effort
        return env, []

    def supports_auto_compile_metadata_generation(self) -> bool:
        """Return whether this provider supports auto-compile metadata generation."""

        return False

    def service_env_overrides(self, *, cwd: Path) -> dict[str, str]:
        """Return provider-specific service env vars derived from the parent env."""

        del cwd
        return {}

    def workspace_instruction_alias_name(self) -> str | None:
        """Return provider-native instruction alias filename, if any."""

        return None

    def ensure_workspace_instruction_alias(self, repo_dir: Path) -> None:
        """Ensure the provider instruction alias points to ``AGENTS.md``."""

        alias_name = self.workspace_instruction_alias_name()
        if not isinstance(alias_name, str) or not alias_name:
            return

        repo_agents = repo_dir / "AGENTS.md"
        if not repo_agents.is_file():
            return

        repo_alias = repo_dir / alias_name
        if repo_alias.is_symlink():
            try:
                if repo_alias.resolve() == repo_agents.resolve():
                    return
            except OSError:
                pass
            try:
                repo_alias.unlink()
            except OSError:
                return
        elif repo_alias.exists():
            return

        try:
            os.symlink("AGENTS.md", repo_alias)
        except OSError:
            try:
                shutil.copy2(repo_agents, repo_alias)
            except OSError:
                pass

    def remove_workspace_instruction_alias_symlink(self, repo_dir: Path) -> None:
        """Remove the provider alias symlink while leaving real files untouched."""

        alias_name = self.workspace_instruction_alias_name()
        if not isinstance(alias_name, str) or not alias_name:
            return

        repo_alias = repo_dir / alias_name
        if not repo_alias.is_symlink():
            return
        try:
            repo_alias.unlink()
        except OSError:
            pass

    def render_stream_event(
        self,
        event: dict,
        *,
        use_color: bool = True,
    ) -> str | None:
        """Convert one provider stream event to human-readable terminal text."""

        c = _ANSI if use_color else _ANSI_OFF
        reset = c["reset"]
        event_type = event.get("type", "")
        item = _event_item(event)
        item_type = _event_item_type(event)
        normalized_item_type = item_type.lower()
        normalized_event_type = str(event_type or "").strip().lower()

        if normalized_item_type.startswith("agent_message"):
            content = _extract_text_like(event.get("delta"), strip=False)
            if not content:
                content = _extract_text_like(item, strip=False)
            if not content:
                content = _extract_text_like(event, strip=False)
            if content:
                return f"{c['agent_label']}[agent]{reset} {c['text']}{content}{reset}"
            return None

        if normalized_item_type.startswith("user_message"):
            content = _extract_text_like(event.get("delta"), strip=False)
            if not content:
                content = _extract_text_like(item, strip=False)
            if not content:
                content = _extract_text_like(event, strip=False)
            if content:
                return f"{c['text']}{content}{reset}"
            return None

        output_event_types = {
            "exec_command_output_delta",
            "exec_command_end",
            "tool_result",
            "command_output",
            "file_change",
            "file_diff",
            "file_update",
            "patch",
            "diff",
        }
        if (
            normalized_item_type in output_event_types
            or normalized_event_type in output_event_types
        ):
            output = _extract_output_preview(event, item)
            if not output and (
                normalized_item_type == "tool_result"
                or normalized_event_type == "tool_result"
            ):
                error_obj = event.get("error")
                if isinstance(error_obj, dict):
                    output = str(error_obj.get("message") or "").strip()
                elif error_obj is not None:
                    output = str(error_obj).strip()
            if not output:
                return None
            return f"{c['tool_out']}{_truncate_tool_output(output)}{reset}"

        if (
            normalized_item_type == "command_execution"
            and normalized_event_type.endswith(".completed")
        ):
            output = _extract_output_preview(event, item)
            if output:
                return f"{c['tool_out']}{_truncate_tool_output(output)}{reset}"
            return None

        if normalized_item_type in {
            "reasoning",
            "reasoning_delta",
            "agent_reasoning",
            "agent_reasoning_delta",
            "thought",
            "thinking",
        }:
            content = _extract_text_like(item, strip=True)
            if not content:
                content = _extract_text_like(event, strip=True)
            cleaned = _strip_thinking_noise(content)
            if cleaned:
                return f"{c['thinking']}[reasoning] {cleaned}{reset}"
            return None

        if normalized_item_type in {
            "command",
            "command_execution",
            "exec",
            "exec_command_begin",
            "tool_call",
            "tool_use",
            "function_call",
        }:
            name = str(
                item.get("name")
                or item.get("tool_name")
                or event.get("name")
                or event.get("tool_name")
                or normalized_item_type
            ).strip()
            command_preview = _extract_command_preview(event)
            if not name and not command_preview:
                return None
            if normalized_item_type == "command_execution" and str(event_type).endswith(
                ".completed"
            ):
                return None
            return (
                f"{c['tool_label']}[{name}]{reset} "
                f"{c['tool_cmd']}{command_preview}{reset}"
            ).strip()

        if event_type == "assistant":
            message = event.get("message")
            if not isinstance(message, dict):
                return None
            content = message.get("content")
            if not isinstance(content, list):
                return None
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")
                if block_type == "text":
                    text = block.get("text", "").strip()
                    if text:
                        parts.append(f"{c['text']}{text}{reset}")
                elif block_type == "thinking":
                    raw = _strip_thinking_noise(block.get("thinking", ""))
                    if raw:
                        parts.append(f"{c['thinking']}{raw}{reset}")
                elif block_type == "tool_use":
                    name = block.get("name", "")
                    command_preview = _format_tool_input_preview(block.get("input"))
                    parts.append(
                        f"{c['tool_label']}[{name}]{reset} "
                        f"{c['tool_cmd']}{command_preview}{reset}"
                    )
            return "\n".join(parts) if parts else None

        if event_type == "user":
            message = event.get("message")
            if not isinstance(message, dict):
                return None
            content = message.get("content")
            if not isinstance(content, list):
                return None
            parts = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_result":
                    continue
                result_content = block.get("content")
                if isinstance(result_content, str) and result_content.strip():
                    output = _truncate_tool_output(result_content.strip())
                    parts.append(f"{c['tool_out']}{output}{reset}")
                elif isinstance(result_content, list):
                    for entry in result_content:
                        if isinstance(entry, dict) and entry.get("type") == "text":
                            text = entry.get("text", "").strip()
                            if text:
                                output = _truncate_tool_output(text)
                                parts.append(f"{c['tool_out']}{output}{reset}")
            return "\n".join(parts) if parts else None

        if event_type == "message":
            role = str(event.get("role") or "").strip().lower()
            if role != "assistant":
                return None
            content = _extract_text_like(event.get("content"), strip=True)
            if content:
                return f"{c['text']}{content}{reset}"
            return None

        if event_type == "thought":
            thought_value = event.get("value")
            if isinstance(thought_value, dict):
                subject = str(thought_value.get("subject") or "").strip()
                description = str(thought_value.get("description") or "").strip()
                if subject and description:
                    content = f"{subject}\n{description}"
                else:
                    content = subject or description
            else:
                content = _extract_text_like(thought_value, strip=True)
            if content:
                cleaned = _strip_thinking_noise(content)
                if cleaned:
                    return f"{c['thinking']}{cleaned}{reset}"
            return None

        if event_type == "tool_use":
            name = str(event.get("tool_name") or event.get("name") or "").strip()
            payload = event.get("parameters")
            if payload is None:
                payload = event.get("input")
            command_preview = _format_tool_input_preview(payload)
            if not name and not command_preview:
                return None
            return (
                f"{c['tool_label']}[{name}]{reset} "
                f"{c['tool_cmd']}{command_preview}{reset}"
            ).strip()

        if event_type == "tool_result":
            output = _extract_text_like(event.get("output"), strip=True)
            if not output:
                error_obj = event.get("error")
                if isinstance(error_obj, dict):
                    output = str(error_obj.get("message") or "").strip()
                elif error_obj is not None:
                    output = str(error_obj).strip()
            if not output:
                return None
            return f"{c['tool_out']}{_truncate_tool_output(output)}{reset}"

        if event_type == "error":
            severity = str(event.get("severity") or "").strip().lower()
            message = str(event.get("message") or "").strip()
            if not message:
                return None
            label = f"[{severity}] " if severity else ""
            return f"{c['tool_out']}{label}{message}{reset}"

        return None

    def extract_assistant_text_chunk(self, event: dict) -> tuple[str, bool]:
        """Extract one assistant text chunk and whether it is a delta chunk."""

        if not isinstance(event, dict):
            return "", False

        item = _event_item(event)
        item_type = _event_item_type(event).lower()
        if item_type.startswith("agent_message"):
            content = _extract_text_like(event.get("delta"), strip=False)
            if not content:
                content = _extract_text_like(item, strip=False)
            if not content:
                content = _extract_text_like(event, strip=False)
            return content, "delta" in item_type or bool(event.get("delta"))

        event_type = event.get("type")
        if event_type == "assistant":
            message = event.get("message")
            if not isinstance(message, dict):
                return "", False
            content = message.get("content")
            if not isinstance(content, list):
                return "", False

            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "text":
                    continue
                text = block.get("text")
                if isinstance(text, str):
                    cleaned = text.strip()
                    if cleaned:
                        parts.append(cleaned)
            return "\n".join(parts).strip(), False

        if event_type == "message":
            role = str(event.get("role") or "").strip().lower()
            if role != "assistant":
                return "", False
            content = _extract_text_like(event.get("content"), strip=False)
            if content:
                return content, bool(event.get("delta"))

        return "", False
