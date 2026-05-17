from __future__ import annotations

import os
import shutil
from pathlib import Path

from fermilink.agent_runtime import (
    DEFAULT_SANDBOX_POLICY,
    normalize_reasoning_effort,
    normalize_sandbox_policy,
)
from fermilink.agents.base import ProviderAgent


_OPENCODE_REASONING_VARIANT = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "max",
}
_TOOL_OUTPUT_MAX_LINES = 5
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


def _truncate_tool_output(text: str, max_lines: int = _TOOL_OUTPUT_MAX_LINES) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    omitted = len(lines) - max_lines
    return "\n".join(lines[:max_lines]) + f"\n... ({omitted} more lines)"


def _opencode_part(event: dict) -> dict:
    part = event.get("part")
    return part if isinstance(part, dict) else {}


def _compact_mapping(value: object) -> str:
    if not isinstance(value, dict):
        return str(value or "").strip()
    for key in (
        "command",
        "cmd",
        "filePath",
        "file_path",
        "path",
        "pattern",
        "query",
        "url",
        "description",
        "content",
    ):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ", ".join(
        f"{key}={candidate}"
        for key, candidate in value.items()
        if isinstance(candidate, (str, int, float, bool))
    )


def _opencode_tool_preview(part: dict) -> str:
    state = part.get("state")
    state_map = state if isinstance(state, dict) else {}
    input_map = state_map.get("input")
    preview = _compact_mapping(input_map)
    if preview:
        return preview
    title = state_map.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    part_title = part.get("title")
    if isinstance(part_title, str) and part_title.strip():
        return part_title.strip()
    return ""


def _opencode_tool_output(part: dict) -> str:
    state = part.get("state")
    if not isinstance(state, dict):
        return ""
    for key in ("output", "error"):
        value = state.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            message = value.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
    return ""


class OpenCodeAgent(ProviderAgent):
    """OpenCode provider adapter with provider-native CLI translation."""

    @property
    def provider(self) -> str:
        return "opencode"

    @property
    def bin_env_key(self) -> str:
        return "FERMILINK_OPENCODE_BIN"

    @property
    def default_binary(self) -> str:
        if os.name == "nt":
            return "opencode.cmd"
        return "opencode"

    def resolve_binary(self, *, provider_bin_override: str | None = None) -> str:
        del provider_bin_override
        raw = os.getenv(self.bin_env_key)
        cleaned = raw.strip() if isinstance(raw, str) else ""
        if cleaned:
            return cleaned

        local_install = Path.home() / ".opencode" / "bin" / self.default_binary
        if shutil.which(self.default_binary) is None and local_install.is_file():
            return str(local_install)
        return self.default_binary

    def uses_json_stream(self) -> bool:
        return True

    def render_stream_event(
        self,
        event: dict,
        *,
        use_color: bool = True,
    ) -> str | None:
        c = _ANSI if use_color else _ANSI_OFF
        reset = c["reset"]
        event_type = str(event.get("type") or "").strip().lower()
        part = _opencode_part(event)
        part_type = str(part.get("type") or "").strip().lower()

        if event_type == "text" or part_type == "text":
            text = str(part.get("text") or event.get("text") or "").strip()
            if text:
                return f"{c['agent_label']}[agent]{reset} {c['text']}{text}{reset}"
            return None

        if event_type == "reasoning" or part_type == "reasoning":
            text = str(part.get("text") or event.get("text") or "").strip()
            if text:
                return f"{c['thinking']}[reasoning] {text}{reset}"
            return None

        if event_type == "tool_use" or part_type == "tool":
            tool_name = str(
                part.get("tool")
                or event.get("tool")
                or part.get("tool_name")
                or event.get("tool_name")
                or "tool"
            ).strip()
            preview = _opencode_tool_preview(part)
            header = (
                f"{c['tool_label']}[{tool_name}]{reset} "
                f"{c['tool_cmd']}{preview}{reset}"
            ).strip()
            output = _opencode_tool_output(part)
            if output:
                return f"{header}\n{c['tool_out']}{_truncate_tool_output(output)}{reset}"
            return header if header else None

        if event_type == "error":
            message = str(event.get("message") or "").strip()
            if message:
                return f"{c['tool_out']}[error] {message}{reset}"
            return None

        return None

    def extract_assistant_text_chunk(self, event: dict) -> tuple[str, bool]:
        part = _opencode_part(event)
        event_type = str(event.get("type") or "").strip().lower()
        part_type = str(part.get("type") or "").strip().lower()
        if event_type == "text" or part_type == "text":
            return str(part.get("text") or event.get("text") or ""), False
        return "", False

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
        normalized_policy = normalize_sandbox_policy(sandbox_policy)
        cmd = [provider_bin, "run", "--dir", str(Path(repo_dir))]

        if json_output:
            cmd.extend(["--format", "json"])

        if (
            normalized_policy == "enforce"
            and isinstance(sandbox_mode, str)
            and sandbox_mode.strip() == "read-only"
        ):
            cmd.extend(["--agent", "plan"])
        elif normalized_policy == "bypass":
            cmd.append("--dangerously-skip-permissions")

        if isinstance(model, str) and model.strip():
            cmd.extend(["--model", model.strip()])

        normalized_effort = normalize_reasoning_effort(reasoning_effort)
        if isinstance(normalized_effort, str) and normalized_effort:
            cmd.extend(
                [
                    "--variant",
                    _OPENCODE_REASONING_VARIANT.get(
                        normalized_effort, normalized_effort
                    ),
                ]
            )

        cmd.append(prompt)
        return cmd
