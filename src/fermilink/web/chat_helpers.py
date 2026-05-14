from __future__ import annotations

import html
from typing import Any

COMPACT_AUXILIARY_MAX_LINES = 5


_NON_ASSISTANT_EVENT_TYPES = {
    "command",
    "command_execution",
    "error",
    "exec",
    "exec_command_begin",
    "exec_command_end",
    "exec_command_output_delta",
    "result",
    "stream_error",
    "system",
    "tool_call",
    "turn.failed",
    "user",
}


def _event_item_type(payload: dict[str, Any]) -> str:
    """Return normalized stream item type from heterogeneous payloads."""

    item = payload.get("item")
    if isinstance(item, dict):
        item_type = item.get("type")
        if isinstance(item_type, str) and item_type.strip():
            return item_type.strip().lower()
    raw_type = payload.get("type")
    if isinstance(raw_type, str) and raw_type.strip():
        return raw_type.strip().lower()
    return ""


def _is_assistant_stream_event(payload: dict[str, Any]) -> bool:
    """Decide whether one stream payload should be treated as assistant text."""

    item_type = _event_item_type(payload)
    if item_type.startswith("agent_message"):
        return True
    if item_type in {"assistant", "assistant_delta", "assistant_message"}:
        return True
    if item_type in _NON_ASSISTANT_EVENT_TYPES:
        return False

    # Provider-native delta payloads may omit explicit assistant item type.
    if any(key in payload for key in ("delta", "content_delta", "message_delta")):
        return True
    return False


def _extract_text(payload: dict[str, Any]) -> str | None:
    """Extract best-effort text content from heterogeneous stream payloads."""

    def from_obj(obj: object) -> str | None:
        if not isinstance(obj, dict):
            return None

        def from_value(value: object) -> str | None:
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict):
                return from_obj(value)
            if isinstance(value, list):
                parts: list[str] = []
                for entry in value:
                    nested = from_value(entry)
                    if nested:
                        parts.append(nested)
                if parts:
                    return "".join(parts)
            return None

        for key in ("text", "content", "message", "raw_content", "summary_text"):
            text = from_value(obj.get(key))
            if text:
                return text
        return None

    if isinstance(payload.get("item"), dict):
        text = from_obj(payload["item"])
        if text:
            return text

    for key in ("delta", "content_delta", "message_delta"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            text = from_obj(value)
            if text:
                return text

    return from_obj(payload)


def _extract_command(payload: dict[str, Any]) -> str | None:
    """Extract command text from a stream payload."""

    for key in ("command", "cmd", "parsed_cmd", "shell_command", "action", "text"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _compact_auxiliary_text(
    text: str,
    *,
    max_lines: int = COMPACT_AUXILIARY_MAX_LINES,
) -> str:
    """Return a display-only first-lines preview for noisy stream payloads."""

    lines = str(text or "").splitlines()
    if max_lines <= 0:
        return ""
    if len(lines) <= max_lines:
        return str(text or "")
    omitted = len(lines) - max_lines
    return "\n".join(lines[:max_lines]) + f"\n... ({omitted} more lines)"


def _format_muted_auxiliary_text(text: str) -> str:
    """Render compact auxiliary output in muted gray for Chainlit markdown."""

    compact = _compact_auxiliary_text(text)
    if not compact:
        return ""
    escaped = html.escape(compact)
    return (
        '<pre style="color:#6b7280;white-space:pre-wrap;">'
        f"{escaped}</pre>"
    )


def _truncate_history_entry(text: str, *, history_entry_max_chars: int) -> str:
    """Truncate one history entry to configured maximum length."""

    if history_entry_max_chars <= 0:
        return ""
    if len(text) <= history_entry_max_chars:
        return text
    overflow = len(text) - history_entry_max_chars
    return f"{text[:history_entry_max_chars]}... ({overflow} chars truncated)"


def _append_history(
    history: list[tuple[str, str]],
    role: str,
    content: str,
    *,
    history_entry_max_chars: int,
    history_max_messages: int,
    history_max_chars: int,
) -> list[tuple[str, str]]:
    """Append one chat turn to bounded session history."""

    content = (content or "").strip()
    if not content:
        return history
    content = _truncate_history_entry(
        content, history_entry_max_chars=history_entry_max_chars
    )
    history.append((role, content))
    if history_max_messages > 0 and len(history) > history_max_messages:
        history = history[-history_max_messages:]
    if history_max_chars > 0:
        total = sum(len(item[1]) for item in history)
        while history and total > history_max_chars:
            dropped = history.pop(0)
            total -= len(dropped[1])
    return history


def _format_history(history: list[tuple[str, str]]) -> str:
    """Render chat history into the prompt transcript format."""

    lines: list[str] = []
    for role, content in history:
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {content}")
    return "\n".join(lines)


def _build_prompt(
    history: list[tuple[str, str]],
    user_text: str,
    *,
    max_prompt_chars: int,
) -> str:
    """Build a size-limited prompt transcript including current user text."""

    temp = history + [("user", (user_text or "").strip())]
    if not temp:
        return user_text
    if max_prompt_chars <= 0:
        return _format_history(temp)
    start = 0
    while start < len(temp):
        candidate = _format_history(temp[start:])
        if len(candidate) <= max_prompt_chars:
            return candidate
        start += 1
    return _format_history([temp[-1]])
