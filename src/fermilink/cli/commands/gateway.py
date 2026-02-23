from __future__ import annotations

import argparse
import html
import json
import mimetypes
import os
import queue
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from fermilink.config import resolve_runtime_root, resolve_workspaces_root


SESSION_STORE_FILENAME = "chat_sessions.json"
SESSION_SCHEMA_VERSION = 1
DEFAULT_GATEWAY_MAX_ITERATIONS = 10
DEFAULT_GATEWAY_WAIT_SECONDS = 1.0
DEFAULT_GATEWAY_MAX_WAIT_SECONDS = 6000.0
DEFAULT_GATEWAY_PID_STALL_SECONDS = 900.0
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
DOCUMENT_SUFFIXES = {".pdf"}
CHECKLIST_ITEM_RE = re.compile(r"^\s*-\s*\[(?P<mark>[xX ])\]\s+(?P<item>.+?)\s*$")
KEY_RESULT_FIELD_RE = re.compile(
    r"^(result_id|metric|value|conditions|evidence_path)\s*:\s*(.*)$",
    re.IGNORECASE,
)
PARAM_SOURCE_FIELD_RE = re.compile(
    r"^(run_id|parameter_or_setting|value|source|evidence_path|notes)\s*:\s*(.*)$",
    re.IGNORECASE,
)
SIM_UNCERTAINTY_FIELD_RE = re.compile(
    r"^(run_id|uncertainty_or_assumption|impact|mitigation_or_next_step|status)\s*:\s*(.*)$",
    re.IGNORECASE,
)
SUPPORTED_EXECUTION_MODES = {"loop", "exec"}
SUPPORTED_WORKFLOW_PROMPT_MODES = {"research", "reproduce"}
SUPPORTED_GATEWAY_RUN_MODES = (
    SUPPORTED_EXECUTION_MODES | SUPPORTED_WORKFLOW_PROMPT_MODES
)
SUPPORTED_REPLY_STYLES = {"summary", "agent", "both"}
WORKFLOW_PROMPT_RE = re.compile(
    r"^\s*fermilink\s+(research|reproduce)\s+(.*?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
LOOP_WAIT_LINE_RE = re.compile(
    r"^\s*<wait_seconds>\s*[0-9]+(?:\.[0-9]+)?\s*</wait_seconds>\s*$"
)
LOOP_PID_LINE_RE = re.compile(r"^\s*<pid_number>\s*[0-9]+\s*</pid_number>\s*$")
LOOP_SLURM_JOB_LINE_RE = re.compile(
    r"^\s*<slurm_job_number>\s*[0-9]+(?:_[0-9]+)?(?:\.[A-Za-z0-9_-]+)?\s*</slurm_job_number>\s*$"
)
MARKDOWN_FENCE_RE = re.compile(
    r"```(?:[ \t]*([A-Za-z0-9_+.#-]+))?[ \t]*\n(.*?)```",
    re.DOTALL,
)
MARKDOWN_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
MARKDOWN_HEADING_LINE_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
MARKDOWN_UL_LINE_RE = re.compile(r"^\s{0,3}[-*+]\s+(.+?)\s*$")
MARKDOWN_OL_LINE_RE = re.compile(r"^\s{0,3}([0-9]+)\.\s+(.+?)\s*$")
MARKDOWN_QUOTE_LINE_RE = re.compile(r"^\s*&gt;\s?(.+?)\s*$")
MARKDOWN_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
MARKDOWN_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
GATEWAY_HELP_TEXT = (
    "Commands:\n"
    "/new [name] - create and switch to a new workspace\n"
    "/use <name-or-id> - switch active workspace\n"
    "/mode <exec|loop|research|reproduce> - switch run mode for normal messages\n"
    "/loopcfg - show/set loop max-iterations and max-wait-seconds\n"
    "/reply <summary|agent|both> - switch final-reply style\n"
    "/status - show gateway/chat run status\n"
    "/where - show active workspace\n"
    "/list - list chat workspaces\n"
    "/help - show commands\n\n"
    "Workflow prompts:\n"
    "fermilink research <prompt-or-file> - run research workflow\n"
    "fermilink reproduce <prompt-or-file> - run reproduce workflow"
)


LoopRunner = Callable[
    [Path, str, "GatewayLoopConfig"], tuple[int, dict[str, Any] | None]
]
ExecRunner = Callable[
    [Path, str, "GatewayLoopConfig"], tuple[int, dict[str, Any] | None]
]
WorkflowRunner = Callable[
    [Path, str, "GatewayLoopConfig"], tuple[int, dict[str, Any] | None]
]
WorkspaceRepoEnsurer = Callable[[Path, bool], None]
LoopIterationHook = Callable[[int, int], None]


@dataclass(frozen=True)
class GatewayLoopConfig:
    package_id: str | None
    sandbox: str | None
    codex_bin: str | None
    max_iterations: int
    wait_seconds: float
    max_wait_seconds: float
    pid_stall_seconds: float
    init_git: bool
    hpc_profile: str | None = None
    loop_iteration_hook: LoopIterationHook | None = None


@dataclass(frozen=True)
class QueuedRunJob:
    job_id: str
    chat_id: str
    chat_key: str
    prompt: str
    mode: str
    workspace_id: str
    workspace_label: str
    max_iterations: int
    max_wait_seconds: float
    queued_at_utc: str


class _TelegramApiClient:
    """Thin Telegram Bot API wrapper using long polling."""

    def __init__(self, *, token: str) -> None:
        timeout = httpx.Timeout(connect=15.0, read=75.0, write=15.0, pool=15.0)
        self._client = httpx.Client(timeout=timeout)
        self._base_url = f"https://api.telegram.org/bot{token}"

    def close(self) -> None:
        self._client.close()

    def get_updates(self, *, offset: int, timeout_seconds: int) -> list[dict[str, Any]]:
        params = {
            "offset": max(0, int(offset)),
            "timeout": max(1, int(timeout_seconds)),
            "allowed_updates": json.dumps(["message"]),
        }
        response = self._client.get(f"{self._base_url}/getUpdates", params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise RuntimeError(f"Telegram getUpdates failed: {payload!r}")
        results = payload.get("result")
        if isinstance(results, list):
            normalized: list[dict[str, Any]] = []
            for item in results:
                if isinstance(item, dict):
                    normalized.append(item)
            return normalized
        return []

    def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        parse_mode: str | None = None,
    ) -> None:
        content = str(text)
        if parse_mode is None:
            content = _truncate_message(content, limit=3900)
        elif len(content) > 4096:
            # HTML/Markdown truncation can break formatting, so use plain fallback.
            fallback = _truncate_message(_strip_html_tags(content), limit=3900)
            self.send_message(chat_id=chat_id, text=fallback, parse_mode=None)
            return

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": content,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        response = self._client.post(f"{self._base_url}/sendMessage", json=payload)
        response.raise_for_status()
        result = response.json()
        if isinstance(result, dict) and result.get("ok") is True:
            return
        if parse_mode:
            # Fallback to plain text if parse-mode rendering fails.
            fallback = _truncate_message(_strip_html_tags(content), limit=3900)
            fallback_payload = {
                "chat_id": chat_id,
                "text": fallback,
                "disable_web_page_preview": True,
            }
            retry = self._client.post(
                f"{self._base_url}/sendMessage",
                json=fallback_payload,
            )
            retry.raise_for_status()
            retry_result = retry.json()
            if isinstance(retry_result, dict) and retry_result.get("ok") is True:
                return
            raise RuntimeError(f"Telegram sendMessage failed: {retry_result!r}")
        raise RuntimeError(f"Telegram sendMessage failed: {result!r}")

    def send_photo(
        self, *, chat_id: str, file_path: Path, caption: str | None = None
    ) -> None:
        if not file_path.is_file():
            raise RuntimeError(f"Telegram photo path does not exist: {file_path}")
        mime = mimetypes.guess_type(file_path.name)[0] or "image/png"
        data: dict[str, str] = {"chat_id": chat_id}
        if caption:
            data["caption"] = _truncate_message(caption, limit=900)
        with file_path.open("rb") as handle:
            files = {"photo": (file_path.name, handle, mime)}
            response = self._client.post(
                f"{self._base_url}/sendPhoto", data=data, files=files
            )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError(f"Telegram sendPhoto failed: {result!r}")

    def send_document(
        self,
        *,
        chat_id: str,
        file_path: Path,
        caption: str | None = None,
    ) -> None:
        if not file_path.is_file():
            raise RuntimeError(f"Telegram document path does not exist: {file_path}")
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        data: dict[str, str] = {"chat_id": chat_id}
        if caption:
            data["caption"] = _truncate_message(caption, limit=900)
        with file_path.open("rb") as handle:
            files = {"document": (file_path.name, handle, mime)}
            response = self._client.post(
                f"{self._base_url}/sendDocument",
                data=data,
                files=files,
            )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError(f"Telegram sendDocument failed: {result!r}")


def _cli():
    from fermilink import cli

    return cli


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_timestamp(raw: str) -> datetime | None:
    token = str(raw or "").strip()
    if not token:
        return None
    normalized = token[:-1] + "+00:00" if token.endswith("Z") else token
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _format_local_timestamp(raw: str) -> str:
    parsed = _parse_iso_timestamp(raw)
    if parsed is None:
        return str(raw or "").strip()
    local_dt = parsed.astimezone()
    tz_name = local_dt.tzname() or "local"
    return f"{local_dt:%Y-%m-%d %H:%M:%S} {tz_name}"


def _truncate_message(text: str, *, limit: int) -> str:
    content = str(text)
    if len(content) <= limit:
        return content
    overflow = len(content) - limit
    return f"{content[:limit]}... ({overflow} more chars)"


def _html_escape(text: str) -> str:
    return html.escape(str(text), quote=False)


def _strip_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", str(text))


def _resolve_session_store_path(raw: str | None) -> Path:
    if raw and raw.strip():
        path = Path(raw.strip()).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        return path
    return resolve_runtime_root() / SESSION_STORE_FILENAME


def _default_gateway_state() -> dict[str, Any]:
    return {
        "schema_version": SESSION_SCHEMA_VERSION,
        "telegram": {
            "offset": 0,
            "chats": {},
        },
    }


def _parse_positive_int(raw: object, *, minimum: int = 1) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= minimum else None
    if isinstance(raw, str) and raw.strip():
        try:
            value = int(raw.strip())
        except ValueError:
            return None
        return value if value >= minimum else None
    return None


def _parse_non_negative_float(raw: object) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        return value if value >= 0 else None
    if isinstance(raw, str) and raw.strip():
        try:
            value = float(raw.strip())
        except ValueError:
            return None
        return value if value >= 0 else None
    return None


def _default_gateway_loop_config() -> GatewayLoopConfig:
    return GatewayLoopConfig(
        package_id=None,
        sandbox=None,
        codex_bin=None,
        max_iterations=DEFAULT_GATEWAY_MAX_ITERATIONS,
        wait_seconds=DEFAULT_GATEWAY_WAIT_SECONDS,
        max_wait_seconds=DEFAULT_GATEWAY_MAX_WAIT_SECONDS,
        pid_stall_seconds=DEFAULT_GATEWAY_PID_STALL_SECONDS,
        init_git=True,
    )


def _normalize_workspace_record(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    workspace_id = str(raw.get("id") or "").strip()
    label = _sanitize_workspace_label(str(raw.get("label") or ""))
    if not workspace_id:
        return None
    created_at = str(raw.get("created_at_utc") or "").strip() or _now_utc_iso()
    last_used_at = str(raw.get("last_used_at_utc") or "").strip() or created_at
    created_via = str(raw.get("created_via") or "").strip() or "unknown"
    return {
        "id": workspace_id,
        "label": label,
        "created_at_utc": created_at,
        "last_used_at_utc": last_used_at,
        "created_via": created_via,
    }


def _normalize_chat_state(raw: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "active_workspace_id": "",
        "workspaces": [],
        "execution_mode": "exec",
        "reply_style": "agent",
        "loop_max_iterations_override": None,
        "loop_max_wait_seconds_override": None,
        "is_running": False,
        "pending_run_count": 0,
        "current_run_id": "",
        "current_run_started_at_utc": "",
        "current_run_mode": "",
        "current_run_workspace_id": "",
        "current_run_workspace_label": "",
        "current_run_prompt_preview": "",
        "last_run_started_at_utc": "",
        "last_run_finished_at_utc": "",
        "last_run_status": "",
        "last_run_reason": "",
        "last_run_exit_code": None,
        "last_run_mode": "",
        "last_run_agent_reply_preview": "",
        "last_run_agent_reply_source": "none",
        "last_run_agent_reply_exact": False,
    }
    if not isinstance(raw, dict):
        return payload

    workspaces_raw = raw.get("workspaces")
    workspace_records: list[dict[str, Any]] = []
    if isinstance(workspaces_raw, list):
        for item in workspaces_raw:
            record = _normalize_workspace_record(item)
            if record is not None:
                workspace_records.append(record)
    payload["workspaces"] = workspace_records

    active_workspace_id = str(raw.get("active_workspace_id") or "").strip()
    if active_workspace_id and any(
        str(ws.get("id")) == active_workspace_id for ws in workspace_records
    ):
        payload["active_workspace_id"] = active_workspace_id
    elif workspace_records:
        payload["active_workspace_id"] = str(workspace_records[-1]["id"])

    mode = str(raw.get("execution_mode") or "").strip().lower()
    if mode in SUPPORTED_GATEWAY_RUN_MODES:
        payload["execution_mode"] = mode
    reply_style = str(raw.get("reply_style") or "").strip().lower()
    if reply_style in SUPPORTED_REPLY_STYLES:
        payload["reply_style"] = reply_style
    loop_max_iterations_override = _parse_positive_int(
        raw.get("loop_max_iterations_override")
    )
    if loop_max_iterations_override is not None:
        payload["loop_max_iterations_override"] = loop_max_iterations_override
    loop_max_wait_seconds_override = _parse_non_negative_float(
        raw.get("loop_max_wait_seconds_override")
    )
    if loop_max_wait_seconds_override is not None:
        payload["loop_max_wait_seconds_override"] = loop_max_wait_seconds_override

    payload["is_running"] = bool(raw.get("is_running"))
    pending_raw = raw.get("pending_run_count")
    if isinstance(pending_raw, int):
        payload["pending_run_count"] = max(0, pending_raw)
    elif isinstance(pending_raw, str) and pending_raw.strip():
        try:
            payload["pending_run_count"] = max(0, int(pending_raw.strip()))
        except ValueError:
            payload["pending_run_count"] = 0

    for key in (
        "current_run_id",
        "current_run_started_at_utc",
        "current_run_mode",
        "current_run_workspace_id",
        "current_run_workspace_label",
        "current_run_prompt_preview",
    ):
        payload[key] = str(raw.get(key) or "").strip()

    for key in (
        "last_run_started_at_utc",
        "last_run_finished_at_utc",
        "last_run_status",
        "last_run_reason",
        "last_run_mode",
        "last_run_agent_reply_preview",
        "last_run_agent_reply_source",
    ):
        value = str(raw.get(key) or "").strip()
        payload[key] = value
    if payload["last_run_agent_reply_source"] not in {
        "exec_last_message",
        "loop_last_informative_turn",
        "loop_final_turn",
        "none",
    }:
        payload["last_run_agent_reply_source"] = "none"

    payload["last_run_agent_reply_exact"] = bool(raw.get("last_run_agent_reply_exact"))

    exit_code_raw = raw.get("last_run_exit_code")
    if isinstance(exit_code_raw, int):
        payload["last_run_exit_code"] = exit_code_raw
    elif isinstance(exit_code_raw, str) and exit_code_raw.strip():
        try:
            payload["last_run_exit_code"] = int(exit_code_raw.strip())
        except ValueError:
            payload["last_run_exit_code"] = None
    return payload


def _normalize_gateway_state(raw: object) -> dict[str, Any]:
    state = _default_gateway_state()
    if not isinstance(raw, dict):
        return state

    schema_version = raw.get("schema_version")
    if isinstance(schema_version, int):
        state["schema_version"] = schema_version

    telegram_raw = raw.get("telegram")
    if not isinstance(telegram_raw, dict):
        return state

    offset_raw = telegram_raw.get("offset")
    if isinstance(offset_raw, int) and offset_raw >= 0:
        state["telegram"]["offset"] = offset_raw

    chats_raw = telegram_raw.get("chats")
    if not isinstance(chats_raw, dict):
        return state
    normalized_chats: dict[str, dict[str, Any]] = {}
    for key, value in chats_raw.items():
        chat_key = str(key).strip()
        if not chat_key:
            continue
        normalized_chats[chat_key] = _normalize_chat_state(value)
    state["telegram"]["chats"] = normalized_chats
    return state


def _load_gateway_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _default_gateway_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_gateway_state()
    return _normalize_gateway_state(payload)


def _save_gateway_state(path: Path, state: dict[str, Any]) -> None:
    normalized = _normalize_gateway_state(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temp.replace(path)


def _telegram_state(state: dict[str, Any]) -> dict[str, Any]:
    telegram = state.get("telegram")
    if not isinstance(telegram, dict):
        state["telegram"] = {"offset": 0, "chats": {}}
        telegram = state["telegram"]
    if not isinstance(telegram.get("offset"), int):
        telegram["offset"] = 0
    if not isinstance(telegram.get("chats"), dict):
        telegram["chats"] = {}
    return telegram


def _workspace_records(chat_state: dict[str, Any]) -> list[dict[str, Any]]:
    records = chat_state.get("workspaces")
    if not isinstance(records, list):
        chat_state["workspaces"] = []
        return chat_state["workspaces"]
    normalized: list[dict[str, Any]] = []
    for item in records:
        record = _normalize_workspace_record(item)
        if record is not None:
            normalized.append(record)
    records[:] = normalized
    return records


def _ensure_chat_state(telegram: dict[str, Any], chat_key: str) -> dict[str, Any]:
    chats = telegram.get("chats")
    if not isinstance(chats, dict):
        chats = {}
        telegram["chats"] = chats
    state = chats.get(chat_key)
    normalized = _normalize_chat_state(state)
    chats[chat_key] = normalized
    return normalized


def _sanitize_workspace_label(raw: str) -> str:
    label = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(raw).strip().lower())
    label = re.sub(r"-{2,}", "-", label).strip("-_")
    return label or "main"


def _next_workspace_label(chat_state: dict[str, Any], preferred: str) -> str:
    normalized_preferred = _sanitize_workspace_label(preferred)
    existing = {
        str(item.get("label") or "").strip().lower()
        for item in _workspace_records(chat_state)
    }
    if normalized_preferred not in existing:
        return normalized_preferred
    idx = 2
    while True:
        candidate = f"{normalized_preferred}-{idx}"
        if candidate not in existing:
            return candidate
        idx += 1


def _build_workspace_id(chat_id: str, label: str) -> str:
    safe_chat = re.sub(r"[^a-zA-Z0-9]+", "-", str(chat_id)).strip("-").lower()
    safe_chat = safe_chat or "chat"
    safe_label = _sanitize_workspace_label(label)
    return f"telegram-{safe_chat}-{safe_label}-{uuid.uuid4().hex[:8]}"


def _touch_workspace(record: dict[str, Any]) -> None:
    record["last_used_at_utc"] = _now_utc_iso()


def _create_workspace(
    chat_state: dict[str, Any],
    *,
    chat_id: str,
    requested_label: str | None,
    created_via: str,
) -> dict[str, Any]:
    records = _workspace_records(chat_state)
    label = _next_workspace_label(chat_state, requested_label or "main")
    existing_ids = {str(item.get("id") or "") for item in records}
    workspace_id = _build_workspace_id(chat_id, label)
    while workspace_id in existing_ids:
        workspace_id = _build_workspace_id(chat_id, label)

    now = _now_utc_iso()
    record = {
        "id": workspace_id,
        "label": label,
        "created_at_utc": now,
        "last_used_at_utc": now,
        "created_via": str(created_via or "unknown"),
    }
    records.append(record)
    chat_state["active_workspace_id"] = workspace_id
    return record


def _active_workspace(chat_state: dict[str, Any]) -> dict[str, Any] | None:
    active_id = str(chat_state.get("active_workspace_id") or "").strip()
    for record in _workspace_records(chat_state):
        if str(record.get("id") or "") == active_id:
            return record
    return None


def _ensure_active_workspace(
    chat_state: dict[str, Any], *, chat_id: str
) -> dict[str, Any]:
    active = _active_workspace(chat_state)
    if active is not None:
        return active
    return _create_workspace(
        chat_state,
        chat_id=chat_id,
        requested_label="main",
        created_via="auto",
    )


def _find_workspace(chat_state: dict[str, Any], token: str) -> dict[str, Any] | None:
    needle = str(token or "").strip()
    if not needle:
        return None

    for record in _workspace_records(chat_state):
        if str(record.get("id") or "") == needle:
            return record

    lowered = needle.lower()
    for record in _workspace_records(chat_state):
        if str(record.get("label") or "").strip().lower() == lowered:
            return record
    return None


def _set_active_workspace(chat_state: dict[str, Any], workspace_id: str) -> bool:
    target = str(workspace_id or "").strip()
    if not target:
        return False
    for record in _workspace_records(chat_state):
        if str(record.get("id") or "") == target:
            chat_state["active_workspace_id"] = target
            _touch_workspace(record)
            return True
    return False


def _format_workspace_short(record: dict[str, Any]) -> str:
    return f"{record['label']} ({record['id']})"


def _format_workspace_list(chat_state: dict[str, Any]) -> str:
    records = _workspace_records(chat_state)
    if not records:
        return "No workspaces for this chat yet."
    active_id = str(chat_state.get("active_workspace_id") or "").strip()
    mode = _effective_execution_mode(chat_state)
    lines = [f"Mode: {mode}", "", "Workspaces:"]
    for record in records:
        marker = "*" if str(record.get("id") or "") == active_id else "-"
        lines.append(f"{marker} {_format_workspace_short(record)}")
    return "\n".join(lines)


def _effective_execution_mode(chat_state: dict[str, Any]) -> str:
    mode = str(chat_state.get("execution_mode") or "exec").strip().lower()
    if mode not in SUPPORTED_GATEWAY_RUN_MODES:
        mode = "exec"
        chat_state["execution_mode"] = mode
    return mode


def _effective_reply_style(chat_state: dict[str, Any]) -> str:
    style = str(chat_state.get("reply_style") or "agent").strip().lower()
    if style not in SUPPORTED_REPLY_STYLES:
        style = "agent"
        chat_state["reply_style"] = style
    return style


def _chat_loop_max_iterations_override(chat_state: dict[str, Any]) -> int | None:
    value = _parse_positive_int(chat_state.get("loop_max_iterations_override"))
    chat_state["loop_max_iterations_override"] = value
    return value


def _chat_loop_max_wait_seconds_override(chat_state: dict[str, Any]) -> float | None:
    value = _parse_non_negative_float(chat_state.get("loop_max_wait_seconds_override"))
    chat_state["loop_max_wait_seconds_override"] = value
    return value


def _effective_loop_config(
    chat_state: dict[str, Any], *, base_loop_config: GatewayLoopConfig
) -> GatewayLoopConfig:
    max_iterations_override = _chat_loop_max_iterations_override(chat_state)
    max_wait_seconds_override = _chat_loop_max_wait_seconds_override(chat_state)
    max_iterations = (
        max_iterations_override
        if max_iterations_override is not None
        else base_loop_config.max_iterations
    )
    max_wait_seconds = (
        max_wait_seconds_override
        if max_wait_seconds_override is not None
        else base_loop_config.max_wait_seconds
    )
    return replace(
        base_loop_config,
        max_iterations=max_iterations,
        max_wait_seconds=max_wait_seconds,
    )


def _format_loop_control_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{float(value)}"


def _build_loopcfg_message(
    chat_state: dict[str, Any], *, base_loop_config: GatewayLoopConfig
) -> str:
    max_iterations_override = _chat_loop_max_iterations_override(chat_state)
    max_wait_override = _chat_loop_max_wait_seconds_override(chat_state)
    effective = _effective_loop_config(chat_state, base_loop_config=base_loop_config)
    iterations_source = (
        "chat override"
        if max_iterations_override is not None
        else "gateway default"
    )
    max_wait_source = (
        "chat override" if max_wait_override is not None else "gateway default"
    )
    return (
        "Current loop controls for normal `loop`/workflow runs:\n"
        f"- max-iterations: {effective.max_iterations} ({iterations_source})\n"
        f"- max-wait-seconds: {_format_loop_control_number(effective.max_wait_seconds)} "
        f"({max_wait_source})\n"
        "Usage: /loopcfg [--max-iterations N] [--max-wait-seconds S] [--reset]"
    )


def _workspace_by_id(
    chat_state: dict[str, Any], workspace_id: str
) -> dict[str, Any] | None:
    target = str(workspace_id or "").strip()
    if not target:
        return None
    for record in _workspace_records(chat_state):
        if str(record.get("id") or "") == target:
            return record
    return None


def _clear_chat_runtime_status(chat_state: dict[str, Any]) -> None:
    chat_state["is_running"] = False
    chat_state["pending_run_count"] = 0
    chat_state["current_run_id"] = ""
    chat_state["current_run_started_at_utc"] = ""
    chat_state["current_run_mode"] = ""
    chat_state["current_run_workspace_id"] = ""
    chat_state["current_run_workspace_label"] = ""
    chat_state["current_run_prompt_preview"] = ""


def _normalize_prompt_preview(text: str, *, limit: int = 160) -> str:
    flat = re.sub(r"\s+", " ", str(text or "").strip())
    return _truncate_message(flat, limit=limit)


def _loop_done_token() -> str:
    try:
        return str(_cli().LOOP_DONE_TOKEN)
    except Exception:
        return "<promise>DONE</promise>"


def _is_loop_control_line(line: str, *, done_token: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped:
        return False
    if stripped == done_token:
        return True
    if LOOP_WAIT_LINE_RE.match(stripped):
        return True
    if LOOP_PID_LINE_RE.match(stripped):
        return True
    if LOOP_SLURM_JOB_LINE_RE.match(stripped):
        return True
    return False


def _contains_done_token_line(text: str, *, done_token: str) -> bool:
    for line in str(text or "").splitlines():
        if str(line).strip() == done_token:
            return True
    return False


def _strip_loop_control_lines(text: str, *, done_token: str | None = None) -> str:
    token = str(done_token or _loop_done_token())
    lines: list[str] = []
    for line in str(text or "").splitlines():
        if _is_loop_control_line(line, done_token=token):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _derive_loop_agent_reply_payload(turns: list[str]) -> dict[str, Any]:
    done_token = _loop_done_token()
    turn_texts = [str(turn or "") for turn in turns]
    non_empty_turns = [turn.strip() for turn in turn_texts if turn.strip()]
    final_raw = non_empty_turns[-1] if non_empty_turns else ""
    payload: dict[str, Any] = {
        "agent_reply_source": "none",
        "agent_reply_exact": False,
        "loop_turn_count": len(turn_texts),
        "loop_done_token_seen": any(
            _contains_done_token_line(turn, done_token=done_token)
            for turn in turn_texts
        ),
        "loop_final_reply_raw": final_raw,
    }
    final_cleaned = _strip_loop_control_lines(final_raw, done_token=done_token)
    if final_cleaned:
        payload["agent_reply_raw"] = final_raw
        payload["agent_reply_text"] = final_cleaned
        payload["agent_reply_source"] = "loop_final_turn"
        payload["agent_reply_exact"] = True
        return payload

    for raw in reversed(non_empty_turns):
        cleaned = _strip_loop_control_lines(raw, done_token=done_token)
        if not cleaned:
            continue
        payload["agent_reply_raw"] = raw
        payload["agent_reply_text"] = cleaned
        payload["agent_reply_source"] = "loop_last_informative_turn"
        payload["agent_reply_exact"] = True
        return payload
    return payload


def _derive_exec_agent_reply_payload(assistant_reply: str) -> dict[str, Any]:
    raw = str(assistant_reply or "").strip()
    if not raw:
        return {
            "agent_reply_source": "none",
            "agent_reply_exact": False,
        }
    return {
        "agent_reply_raw": raw,
        "agent_reply_text": raw,
        "agent_reply_source": "exec_last_message",
        "agent_reply_exact": True,
    }


def _extract_outcome_agent_reply_text(outcome: dict[str, Any] | None) -> str:
    if not isinstance(outcome, dict):
        return ""
    text = str(outcome.get("agent_reply_text") or "").strip()
    if text:
        return text
    raw = str(outcome.get("agent_reply_raw") or "").strip()
    if not raw:
        return ""
    source = str(outcome.get("agent_reply_source") or "").strip().lower()
    if source.startswith("loop_"):
        return _strip_loop_control_lines(raw)
    return raw


def _record_last_run_agent_reply(
    chat_state: dict[str, Any],
    outcome: dict[str, Any] | None,
) -> None:
    reply_text = _extract_outcome_agent_reply_text(outcome)
    source = "none"
    exact = False
    if isinstance(outcome, dict):
        source = str(outcome.get("agent_reply_source") or "none").strip() or "none"
        exact = bool(outcome.get("agent_reply_exact"))
    if source not in {
        "exec_last_message",
        "loop_last_informative_turn",
        "loop_final_turn",
        "none",
    }:
        source = "none"
    chat_state["last_run_agent_reply_preview"] = (
        _normalize_prompt_preview(reply_text) if reply_text else ""
    )
    chat_state["last_run_agent_reply_source"] = source
    chat_state["last_run_agent_reply_exact"] = bool(exact and reply_text)


def _format_agent_markdown_inline(escaped_text: str) -> str:
    content = str(escaped_text or "")
    content = MARKDOWN_BOLD_RE.sub(
        lambda match: f"<b>{match.group(1) or match.group(2) or ''}</b>",
        content,
    )
    content = MARKDOWN_ITALIC_RE.sub(
        lambda match: f"<i>{match.group(1) or ''}</i>",
        content,
    )
    content = MARKDOWN_LINK_RE.sub(
        lambda match: (
            f'<a href="{_html_escape(match.group(2) or "")}">'
            f"{match.group(1) or ''}</a>"
        ),
        content,
    )
    return content


def _render_agent_markdown_html(markdown_text: str) -> str:
    text = str(markdown_text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return ""

    placeholders: dict[str, str] = {}
    inline_index = 0
    fence_index = 0

    def _replace_fence(match: re.Match[str]) -> str:
        nonlocal fence_index
        language = str(match.group(1) or "").strip()
        code_text = str(match.group(2) or "").rstrip("\n")
        token = f"@@TG_FENCE_{fence_index}@@"
        fence_index += 1
        if language:
            placeholders[token] = (
                f'<pre><code class="language-{_html_escape(language)}">'
                f"{_html_escape(code_text)}</code></pre>"
            )
        else:
            placeholders[token] = f"<pre>{_html_escape(code_text)}</pre>"
        return token

    text = MARKDOWN_FENCE_RE.sub(_replace_fence, text)

    def _replace_inline_code(match: re.Match[str]) -> str:
        nonlocal inline_index
        token = f"@@TG_INLINE_{inline_index}@@"
        inline_index += 1
        code_text = str(match.group(1) or "")
        placeholders[token] = f"<code>{_html_escape(code_text)}</code>"
        return token

    text = MARKDOWN_INLINE_CODE_RE.sub(_replace_inline_code, text)
    escaped_text = _html_escape(text)

    rendered_lines: list[str] = []
    for raw_line in escaped_text.splitlines():
        line = str(raw_line or "")
        stripped = line.strip()
        if not stripped:
            rendered_lines.append("")
            continue

        heading_match = MARKDOWN_HEADING_LINE_RE.match(line)
        if heading_match is not None:
            heading_text = _format_agent_markdown_inline(
                str(heading_match.group(1) or "")
            )
            rendered_lines.append(f"<b>{heading_text}</b>")
            continue

        ul_match = MARKDOWN_UL_LINE_RE.match(line)
        if ul_match is not None:
            item_text = _format_agent_markdown_inline(str(ul_match.group(1) or ""))
            rendered_lines.append(f"• {item_text}")
            continue

        ol_match = MARKDOWN_OL_LINE_RE.match(line)
        if ol_match is not None:
            number = str(ol_match.group(1) or "").strip() or "1"
            item_text = _format_agent_markdown_inline(str(ol_match.group(2) or ""))
            rendered_lines.append(f"{number}. {item_text}")
            continue

        quote_match = MARKDOWN_QUOTE_LINE_RE.match(line)
        if quote_match is not None:
            quote_text = _format_agent_markdown_inline(str(quote_match.group(1) or ""))
            rendered_lines.append(f"<i>&gt; {quote_text}</i>")
            continue

        rendered_lines.append(_format_agent_markdown_inline(line))

    rendered = "\n".join(rendered_lines).strip()
    if not rendered:
        return ""
    for token, html_value in placeholders.items():
        rendered = rendered.replace(token, html_value)
    return rendered


def _build_agent_reply_section(reply_text: str) -> str:
    rendered = _render_agent_markdown_html(_truncate_message(reply_text, limit=2600))
    if not rendered:
        rendered = _html_escape(_truncate_message(reply_text, limit=2600))
    return "\n".join(["<b>Agent Reply</b>", rendered])


def _compose_run_completion_message(
    *,
    summary: str,
    outcome: dict[str, Any] | None,
    reply_style: str,
) -> str:
    style = str(reply_style or "agent").strip().lower()
    if style not in SUPPORTED_REPLY_STYLES:
        style = "agent"
    if style == "summary":
        return summary

    reply_text = _extract_outcome_agent_reply_text(outcome)
    if not reply_text:
        return summary

    agent_section = _build_agent_reply_section(reply_text)
    if style == "agent":
        return agent_section
    return f"{agent_section}\n\n{summary}"


def _normalize_allow_token(token: str) -> str:
    value = str(token or "").strip()
    if not value:
        return ""
    if value.startswith("@"):
        value = value[1:]
    if re.fullmatch(r"-?\d+", value):
        return value
    return value.lower()


def _parse_allow_from(allow_from: list[str] | None) -> set[str]:
    values: list[str] = []
    env_raw = os.getenv("FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM")
    if env_raw and env_raw.strip():
        values.append(env_raw.strip())
    if isinstance(allow_from, list):
        values.extend(str(item) for item in allow_from if str(item).strip())

    parsed: set[str] = set()
    for raw in values:
        for chunk in re.split(r"[\s,]+", raw.strip()):
            normalized = _normalize_allow_token(chunk)
            if normalized:
                parsed.add(normalized)
    return parsed


def _is_sender_allowed(
    *,
    sender_id: str,
    username: str | None,
    allowed_tokens: set[str],
) -> bool:
    if not allowed_tokens:
        return True
    normalized_id = _normalize_allow_token(sender_id)
    if normalized_id and normalized_id in allowed_tokens:
        return True
    normalized_user = _normalize_allow_token(username or "")
    if normalized_user and normalized_user in allowed_tokens:
        return True
    return False


def _parse_gateway_command(text: str) -> tuple[str | None, str]:
    stripped = str(text or "").strip()
    if not stripped.startswith("/"):
        return None, stripped
    token, _, remainder = stripped.partition(" ")
    command = token.split("@", 1)[0].lower()
    if command in {
        "/new",
        "/use",
        "/mode",
        "/loopcfg",
        "/reply",
        "/status",
        "/where",
        "/list",
        "/help",
        "/start",
    }:
        return command, remainder.strip()
    return None, stripped


def _parse_loopcfg_updates(
    argument: str,
) -> tuple[int | None, float | None, bool, str | None]:
    tokens = [str(token) for token in str(argument or "").split() if str(token)]
    max_iterations: int | None = None
    max_wait_seconds: float | None = None
    reset = False

    idx = 0
    while idx < len(tokens):
        token = tokens[idx]

        if token == "--reset":
            reset = True
            idx += 1
            continue

        flag = token
        value_token: str | None = None
        if "=" in token:
            flag, _, value_token = token.partition("=")
        else:
            if flag in {"--max-iterations", "--max-wait-seconds"}:
                idx += 1
                if idx >= len(tokens):
                    return (
                        None,
                        None,
                        False,
                        f"Missing value for {flag}.",
                    )
                value_token = tokens[idx]

        if flag == "--max-iterations":
            parsed = _parse_positive_int(value_token)
            if parsed is None:
                return (
                    None,
                    None,
                    False,
                    "--max-iterations must be an integer >= 1.",
                )
            max_iterations = parsed
        elif flag == "--max-wait-seconds":
            parsed_wait = _parse_non_negative_float(value_token)
            if parsed_wait is None:
                return (
                    None,
                    None,
                    False,
                    "--max-wait-seconds must be a number >= 0.",
                )
            max_wait_seconds = parsed_wait
        else:
            return (
                None,
                None,
                False,
                f"Unsupported option: {token}",
            )
        idx += 1

    if reset and (max_iterations is not None or max_wait_seconds is not None):
        return (
            None,
            None,
            False,
            "Cannot combine --reset with --max-iterations/--max-wait-seconds.",
        )
    if not reset and max_iterations is None and max_wait_seconds is None:
        return (
            None,
            None,
            False,
            "No loop-control option provided.",
        )
    return max_iterations, max_wait_seconds, reset, None


def _parse_gateway_workflow_prompt(text: str) -> tuple[str, str] | None:
    match = WORKFLOW_PROMPT_RE.match(str(text or ""))
    if match is None:
        return None
    mode = str(match.group(1) or "").strip().lower()
    prompt = str(match.group(2) or "").strip()
    if mode not in SUPPORTED_WORKFLOW_PROMPT_MODES or not prompt:
        return None
    return mode, prompt


def _resolve_prompt_mode_and_text(
    *,
    chat_state: dict[str, Any],
    text: str,
) -> tuple[str, str]:
    workflow_request = _parse_gateway_workflow_prompt(text)
    if workflow_request is not None:
        return workflow_request
    return _effective_execution_mode(chat_state), str(text or "").strip()


def _build_loop_config(args: argparse.Namespace) -> GatewayLoopConfig:
    hpc_profile_raw = getattr(args, "hpc_profile", None)
    hpc_profile: str | None = None
    if isinstance(hpc_profile_raw, str) and hpc_profile_raw.strip():
        resolved_hpc_profile = Path(hpc_profile_raw.strip()).expanduser()
        if not resolved_hpc_profile.is_absolute():
            resolved_hpc_profile = (Path.cwd() / resolved_hpc_profile).resolve()
        hpc_profile = str(resolved_hpc_profile)

    return GatewayLoopConfig(
        package_id=getattr(args, "package_id", None),
        sandbox=getattr(args, "sandbox", None),
        codex_bin=getattr(args, "codex_bin", None),
        max_iterations=int(
            getattr(args, "max_iterations", DEFAULT_GATEWAY_MAX_ITERATIONS)
        ),
        wait_seconds=float(getattr(args, "wait_seconds", DEFAULT_GATEWAY_WAIT_SECONDS)),
        max_wait_seconds=float(
            getattr(args, "max_wait_seconds", DEFAULT_GATEWAY_MAX_WAIT_SECONDS)
        ),
        pid_stall_seconds=float(
            getattr(args, "pid_stall_seconds", DEFAULT_GATEWAY_PID_STALL_SECONDS)
        ),
        init_git=bool(getattr(args, "init_git", True)),
        hpc_profile=hpc_profile,
    )


def _ensure_workspace_repo(repo_dir: Path, init_git: bool) -> None:
    cli = _cli()
    runner_app = cli._load_runner_app_module()
    source_dir = runner_app._resolve_source_dir()

    if repo_dir.exists() and not repo_dir.is_dir():
        raise cli.PackageError(
            f"Workspace repo path exists but is not a directory: {repo_dir}"
        )

    if not repo_dir.exists():
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_dir, repo_dir, dirs_exist_ok=True)

    runner_app._ensure_template_agents_file(source_dir, repo_dir)
    if init_git:
        runner_app._ensure_git_repo(repo_dir)
    elif not runner_app._is_valid_git_repo(repo_dir):
        raise cli.PackageError(
            f"Workspace repo is not a git repository and --no-init-git is set: {repo_dir}"
        )


def _run_loop_in_workspace(
    repo_dir: Path,
    prompt: str,
    loop_config: GatewayLoopConfig,
) -> tuple[int, dict[str, Any] | None]:
    cli = _cli()
    captured_assistant_turns: list[str] = []
    original_run_exec_chat_turn = getattr(cli, "_run_exec_chat_turn", None)
    should_capture_turns = callable(original_run_exec_chat_turn)

    def _run_exec_chat_turn_capture(*args: Any, **kwargs: Any) -> dict[str, object]:
        result = original_run_exec_chat_turn(*args, **kwargs)  # type: ignore[misc]
        if isinstance(result, dict):
            captured_assistant_turns.append(str(result.get("assistant_text") or ""))
        return result

    loop_args = argparse.Namespace(
        command="loop",
        prompt=[prompt],
        package_id=loop_config.package_id,
        sandbox=loop_config.sandbox,
        hpc_profile=loop_config.hpc_profile,
        codex_bin=loop_config.codex_bin,
        max_iterations=loop_config.max_iterations,
        wait_seconds=loop_config.wait_seconds,
        max_wait_seconds=loop_config.max_wait_seconds,
        pid_stall_seconds=loop_config.pid_stall_seconds,
        init_git=loop_config.init_git,
        no_init_git=not loop_config.init_git,
        workflow_prompt_preamble=None,
        _fermilink_loop_iteration_hook=loop_config.loop_iteration_hook,
    )
    previous_cwd = Path.cwd()
    try:
        if should_capture_turns:
            setattr(cli, "_run_exec_chat_turn", _run_exec_chat_turn_capture)
        os.chdir(repo_dir)
        code = cli._cmd_loop(loop_args)
    finally:
        if should_capture_turns:
            setattr(cli, "_run_exec_chat_turn", original_run_exec_chat_turn)
        os.chdir(previous_cwd)
    outcome_raw = getattr(loop_args, "_fermilink_loop_outcome", None)
    outcome = dict(outcome_raw) if isinstance(outcome_raw, dict) else {}
    outcome.update(_derive_loop_agent_reply_payload(captured_assistant_turns))
    return int(code), outcome


def _run_exec_in_workspace(
    repo_dir: Path,
    prompt: str,
    loop_config: GatewayLoopConfig,
) -> tuple[int, dict[str, Any] | None]:
    cli = _cli()
    exec_args = argparse.Namespace(
        command="exec",
        prompt=[prompt],
        package_id=loop_config.package_id,
        sandbox=loop_config.sandbox,
        hpc_profile=loop_config.hpc_profile,
        codex_bin=loop_config.codex_bin,
        init_git=loop_config.init_git,
        no_init_git=not loop_config.init_git,
    )
    assistant_reply = ""
    original_build_exec_command = getattr(cli, "build_exec_command", None)
    inject_option = getattr(cli, "_inject_exec_option_before_prompt", None)
    should_capture_last_message = callable(original_build_exec_command) and callable(
        inject_option
    )
    previous_cwd = Path.cwd()
    if should_capture_last_message:
        with cli.tempfile.TemporaryDirectory(
            prefix="fermilink-gateway-exec-"
        ) as temp_dir:
            last_message_path = Path(temp_dir) / "last_message.txt"

            def _build_exec_command_with_last_message(
                *,
                provider: str,
                provider_bin: str,
                repo_dir: Path,
                prompt: str,
                sandbox_policy: str = "enforce",
                sandbox_mode: str | None = None,
                json_output: bool = True,
            ) -> list[str]:
                command = original_build_exec_command(
                    provider=provider,
                    provider_bin=provider_bin,
                    repo_dir=repo_dir,
                    prompt=prompt,
                    sandbox_policy=sandbox_policy,
                    sandbox_mode=sandbox_mode,
                    json_output=json_output,
                )
                if json_output:
                    return command
                return inject_option(
                    command,
                    "--output-last-message",
                    str(last_message_path),
                )

            try:
                setattr(
                    cli, "build_exec_command", _build_exec_command_with_last_message
                )
                os.chdir(repo_dir)
                code = cli._cmd_exec(exec_args)
            finally:
                setattr(cli, "build_exec_command", original_build_exec_command)
                os.chdir(previous_cwd)

            try:
                assistant_reply = last_message_path.read_text(encoding="utf-8").strip()
            except OSError:
                assistant_reply = ""
    else:
        try:
            os.chdir(repo_dir)
            code = cli._cmd_exec(exec_args)
        finally:
            os.chdir(previous_cwd)

    agent_reply_payload = _derive_exec_agent_reply_payload(assistant_reply)
    if int(code) == 0:
        return int(code), {
            "status": "done",
            "reason": "exec_completed",
            **agent_reply_payload,
        }
    return int(code), {
        "status": "provider_failure",
        "reason": f"provider_exit_code_{int(code)}",
        "provider_exit_code": int(code),
        **agent_reply_payload,
    }


def _run_workflow_in_workspace(
    repo_dir: Path,
    prompt: str,
    loop_config: GatewayLoopConfig,
    *,
    workflow_name: str,
) -> tuple[int, dict[str, Any] | None]:
    cli = _cli()
    workflow = str(workflow_name or "").strip().lower()
    if workflow not in SUPPORTED_WORKFLOW_PROMPT_MODES:
        raise cli.PackageError(f"Unsupported workflow mode: {workflow_name}")
    workflow_args = argparse.Namespace(
        command=workflow,
        prompt=[prompt],
        package_id=loop_config.package_id,
        sandbox=loop_config.sandbox,
        codex_bin=loop_config.codex_bin,
        task_max_runs=5,
        planner_max_tries=2,
        auditor_max_tries=2,
        max_iterations=loop_config.max_iterations,
        wait_seconds=loop_config.wait_seconds,
        max_wait_seconds=loop_config.max_wait_seconds,
        pid_stall_seconds=loop_config.pid_stall_seconds,
        hpc_profile=loop_config.hpc_profile,
        plan_only=False,
        report_only=False,
        skip_report=False,
        resume=True,
        init_git=loop_config.init_git,
        no_init_git=not loop_config.init_git,
    )
    previous_cwd = Path.cwd()
    try:
        os.chdir(repo_dir)
        if workflow == "research":
            code = cli._cmd_research(workflow_args)
        else:
            code = cli._cmd_reproduce(workflow_args)
    finally:
        os.chdir(previous_cwd)

    if int(code) == 0:
        return int(code), {
            "status": "done",
            "reason": f"{workflow}_completed",
        }
    return int(code), {
        "status": "provider_failure",
        "reason": f"{workflow}_exit_code_{int(code)}",
        "provider_exit_code": int(code),
    }


def _run_research_in_workspace(
    repo_dir: Path,
    prompt: str,
    loop_config: GatewayLoopConfig,
) -> tuple[int, dict[str, Any] | None]:
    return _run_workflow_in_workspace(
        repo_dir=repo_dir,
        prompt=prompt,
        loop_config=loop_config,
        workflow_name="research",
    )


def _run_reproduce_in_workspace(
    repo_dir: Path,
    prompt: str,
    loop_config: GatewayLoopConfig,
) -> tuple[int, dict[str, Any] | None]:
    return _run_workflow_in_workspace(
        repo_dir=repo_dir,
        prompt=prompt,
        loop_config=loop_config,
        workflow_name="reproduce",
    )


def _extract_key_results(memory_text: str, *, max_items: int = 5) -> list[str]:
    lines = memory_text.splitlines()
    in_key_results = False
    results: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("### "):
            if stripped == "### Key results":
                in_key_results = True
                continue
            if in_key_results:
                break
        if not in_key_results:
            continue
        if not stripped.startswith("- "):
            continue
        item = stripped[2:].strip()
        lowered = item.lower()
        if not item:
            continue
        if "(result_id | metric | value | conditions | evidence_path)" in lowered:
            continue
        results.append(item)
    return results[-max_items:]


def _extract_plan_progress(
    memory_text: str, *, max_done: int = 4, max_pending: int = 2
) -> tuple[list[str], list[str]]:
    lines = memory_text.splitlines()
    in_plan = False
    done: list[str] = []
    pending: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("### "):
            if stripped == "### Plan":
                in_plan = True
                continue
            if in_plan:
                break
        if not in_plan:
            continue
        match = CHECKLIST_ITEM_RE.match(stripped)
        if not match:
            continue
        item = str(match.group("item") or "").strip()
        if not item or item.lower() == "(fill in a small checklist plan)":
            continue
        marker = str(match.group("mark") or "").strip().lower()
        if marker == "x":
            done.append(item)
        else:
            pending.append(item)
    if max_done > 0:
        done = done[-max_done:]
    if max_pending > 0:
        pending = pending[:max_pending]
    return done, pending


def _extract_memory_section_items(
    memory_text: str,
    *,
    heading: str,
    placeholder_markers: tuple[str, ...],
    max_items: int = 5,
) -> list[str]:
    lines = memory_text.splitlines()
    in_section = False
    items: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("### "):
            if stripped == heading:
                in_section = True
                continue
            if in_section:
                break
        if not in_section:
            continue
        if not stripped.startswith("- "):
            continue
        item = stripped[2:].strip()
        lowered = item.lower()
        if not item:
            continue
        if item.startswith("(") and all(marker in lowered for marker in placeholder_markers):
            continue
        items.append(item)
    return items[-max_items:]


def _split_key_result_item(item: str) -> tuple[str, str, str, str, str]:
    normalized = str(item).replace("`", "").strip()
    parts = [part.strip() for part in normalized.split("|")]
    if any(KEY_RESULT_FIELD_RE.match(part or "") for part in parts):
        fields = {
            "result_id": "",
            "metric": "",
            "value": "",
            "conditions": "",
            "evidence_path": "",
        }
        current_field: str | None = None
        for token in parts:
            text = token.strip()
            if not text:
                continue
            match = KEY_RESULT_FIELD_RE.match(text)
            if match is not None:
                current_field = str(match.group(1)).lower()
                text = str(match.group(2) or "").strip()
            elif current_field is None:
                current_field = "metric"

            if current_field is None or not text:
                continue
            existing = str(fields.get(current_field) or "").strip()
            if existing:
                fields[current_field] = f"{existing} | {text}"
            else:
                fields[current_field] = text
        return (
            str(fields["result_id"]),
            str(fields["metric"]),
            str(fields["value"]),
            str(fields["conditions"]),
            str(fields["evidence_path"]),
        )

    if len(parts) >= 5:
        result_id = parts[0]
        metric = parts[1]
        value = " | ".join(parts[2:-2]).strip()
        conditions = parts[-2]
        evidence_path = parts[-1]
        return result_id, metric, value, conditions, evidence_path
    if len(parts) == 4:
        result_id, metric, value, conditions = parts
        return result_id, metric, value, conditions, ""
    if len(parts) == 3:
        result_id, metric, value = parts
        return result_id, metric, value, "", ""
    return "", normalized, "", "", ""


def _split_parameter_source_item(item: str) -> tuple[str, str, str, str, str, str]:
    normalized = str(item).replace("`", "").strip()
    parts = [part.strip() for part in normalized.split("|")]
    if any(PARAM_SOURCE_FIELD_RE.match(part or "") for part in parts):
        fields = {
            "run_id": "",
            "parameter_or_setting": "",
            "value": "",
            "source": "",
            "evidence_path": "",
            "notes": "",
        }
        current_field: str | None = None
        for token in parts:
            text = token.strip()
            if not text:
                continue
            match = PARAM_SOURCE_FIELD_RE.match(text)
            if match is not None:
                current_field = str(match.group(1)).lower()
                text = str(match.group(2) or "").strip()
            elif current_field is None:
                current_field = "parameter_or_setting"

            if current_field is None or not text:
                continue
            existing = str(fields.get(current_field) or "").strip()
            if existing:
                fields[current_field] = f"{existing} | {text}"
            else:
                fields[current_field] = text
        return (
            str(fields["run_id"]),
            str(fields["parameter_or_setting"]),
            str(fields["value"]),
            str(fields["source"]),
            str(fields["evidence_path"]),
            str(fields["notes"]),
        )

    if len(parts) >= 6:
        run_id = parts[0]
        parameter = parts[1]
        value = parts[2]
        source = parts[3]
        evidence_path = parts[4]
        notes = " | ".join(parts[5:]).strip()
        return run_id, parameter, value, source, evidence_path, notes
    if len(parts) == 5:
        run_id, parameter, value, source, evidence_path = parts
        return run_id, parameter, value, source, evidence_path, ""
    if len(parts) == 4:
        run_id, parameter, value, source = parts
        return run_id, parameter, value, source, "", ""
    if len(parts) == 3:
        run_id, parameter, value = parts
        return run_id, parameter, value, "", "", ""
    if len(parts) == 2:
        run_id, parameter = parts
        return run_id, parameter, "", "", "", ""
    return "", normalized, "", "", "", ""


def _split_simulation_uncertainty_item(item: str) -> tuple[str, str, str, str, str]:
    normalized = str(item).replace("`", "").strip()
    parts = [part.strip() for part in normalized.split("|")]
    if any(SIM_UNCERTAINTY_FIELD_RE.match(part or "") for part in parts):
        fields = {
            "run_id": "",
            "uncertainty_or_assumption": "",
            "impact": "",
            "mitigation_or_next_step": "",
            "status": "",
        }
        current_field: str | None = None
        for token in parts:
            text = token.strip()
            if not text:
                continue
            match = SIM_UNCERTAINTY_FIELD_RE.match(text)
            if match is not None:
                current_field = str(match.group(1)).lower()
                text = str(match.group(2) or "").strip()
            elif current_field is None:
                current_field = "uncertainty_or_assumption"

            if current_field is None or not text:
                continue
            existing = str(fields.get(current_field) or "").strip()
            if existing:
                fields[current_field] = f"{existing} | {text}"
            else:
                fields[current_field] = text
        return (
            str(fields["run_id"]),
            str(fields["uncertainty_or_assumption"]),
            str(fields["impact"]),
            str(fields["mitigation_or_next_step"]),
            str(fields["status"]),
        )

    if len(parts) >= 5:
        run_id = parts[0]
        uncertainty = parts[1]
        impact = parts[2]
        mitigation = parts[3]
        status = " | ".join(parts[4:]).strip()
        return run_id, uncertainty, impact, mitigation, status
    if len(parts) == 4:
        run_id, uncertainty, impact, mitigation = parts
        return run_id, uncertainty, impact, mitigation, ""
    if len(parts) == 3:
        run_id, uncertainty, impact = parts
        return run_id, uncertainty, impact, "", ""
    if len(parts) == 2:
        run_id, uncertainty = parts
        return run_id, uncertainty, "", "", ""
    return "", normalized, "", "", ""


def _parameter_source_run_id(item: str) -> str:
    run_id, _, _, _, _, _ = _split_parameter_source_item(item)
    return str(run_id).strip()


def _simulation_uncertainty_run_id(item: str) -> str:
    run_id, _, _, _, _ = _split_simulation_uncertainty_item(item)
    return str(run_id).strip()


def _filter_items_to_latest_run_id(
    items: list[str], *, run_id_resolver: Callable[[str], str]
) -> list[str]:
    if not items:
        return []
    latest_run_id = ""
    for raw in reversed(items):
        candidate = str(run_id_resolver(raw)).strip()
        if candidate:
            latest_run_id = candidate
            break
    if not latest_run_id:
        return items
    filtered = [
        item
        for item in items
        if str(run_id_resolver(item)).strip() == latest_run_id
    ]
    return filtered if filtered else items


def _select_key_results_for_summary(
    key_results: list[str], *, max_items: int = 4
) -> list[str]:
    if max_items <= 0 or not key_results:
        return []
    latest = str(key_results[-1] or "").strip()
    if not latest:
        return []
    return [latest]


def _format_key_results_human(
    key_results: list[str], *, max_items: int = 4
) -> list[tuple[str, str | None]]:
    lines: list[tuple[str, str | None]] = []
    for item in key_results[:max_items]:
        result_id, metric, value, conditions, _ = _split_key_result_item(item)
        metric_text = metric or result_id or "result"
        if value:
            entry = f"{metric_text}: {value}"
        else:
            entry = metric_text
        condition_text = conditions.strip()
        if condition_text:
            lines.append((entry, _truncate_message(condition_text, limit=140)))
        else:
            lines.append((entry, None))
    return lines


def _format_parameter_source_mapping_human(
    items: list[str], *, max_items: int = 3
) -> list[tuple[str, str | None]]:
    lines: list[tuple[str, str | None]] = []
    for item in items[:max_items]:
        run_id, parameter, value, source, evidence_path, notes = (
            _split_parameter_source_item(item)
        )
        title = parameter or run_id or "parameter/source entry"
        if run_id and parameter:
            title = f"[{run_id}] {title}"
        if value:
            title = f"{title}: {value}"
        detail_parts: list[str] = []
        if source:
            detail_parts.append(f"source: {source}")
        if evidence_path:
            detail_parts.append(f"evidence: {evidence_path}")
        if notes:
            detail_parts.append(notes)
        detail = (
            _truncate_message("; ".join(detail_parts), limit=220)
            if detail_parts
            else None
        )
        lines.append((_truncate_message(title, limit=220), detail))
    return lines


def _format_simulation_uncertainty_human(
    items: list[str], *, max_items: int = 3
) -> list[tuple[str, str | None]]:
    lines: list[tuple[str, str | None]] = []
    for item in items[:max_items]:
        run_id, uncertainty, impact, mitigation, status = (
            _split_simulation_uncertainty_item(item)
        )
        title = uncertainty or run_id or "uncertainty entry"
        if run_id and uncertainty:
            title = f"[{run_id}] {title}"
        detail_parts: list[str] = []
        if impact:
            detail_parts.append(f"impact: {impact}")
        if mitigation:
            detail_parts.append(f"next step: {mitigation}")
        if status:
            detail_parts.append(f"status: {status}")
        detail = (
            _truncate_message("; ".join(detail_parts), limit=220)
            if detail_parts
            else None
        )
        lines.append((_truncate_message(title, limit=220), detail))
    return lines


def _collect_recent_artifacts(repo_dir: Path, *, max_items: int = 4) -> list[str]:
    roots = [repo_dir / "projects", repo_dir / "outputs"]
    files: list[tuple[float, Path]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                files.append((path.stat().st_mtime, path))
            except OSError:
                continue
    files.sort(key=lambda item: item[0], reverse=True)
    selected: list[str] = []
    seen: set[str] = set()
    for _, path in files:
        try:
            rel = path.relative_to(repo_dir).as_posix()
        except ValueError:
            rel = path.as_posix()
        if rel in seen:
            continue
        seen.add(rel)
        selected.append(rel)
        if len(selected) >= max_items:
            break
    return selected


def _resolve_repo_relative_file(repo_dir: Path, raw_path: str) -> Path | None:
    token = str(raw_path or "").strip().strip("`")
    if not token:
        return None
    path = Path(token)
    if not path.is_absolute():
        path = (repo_dir / path).resolve()
    else:
        path = path.resolve()
    try:
        path.relative_to(repo_dir.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path


def _collect_key_result_media(repo_dir: Path, key_results: list[str]) -> list[Path]:
    media: list[Path] = []
    seen: set[Path] = set()
    for item in key_results:
        _, _, _, _, evidence_path = _split_key_result_item(item)
        resolved = _resolve_repo_relative_file(repo_dir, evidence_path)
        if resolved is None:
            continue
        suffix = resolved.suffix.lower()
        if suffix not in IMAGE_SUFFIXES and suffix not in DOCUMENT_SUFFIXES:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        media.append(resolved)
    return media


def _collect_recent_media(
    repo_dir: Path,
    *,
    since_epoch: float | None,
    max_images: int = 3,
    max_documents: int = 1,
) -> tuple[list[Path], list[Path]]:
    roots = [repo_dir / "projects", repo_dir / "outputs"]
    images: list[tuple[float, Path]] = []
    documents: list[tuple[float, Path]] = []
    since = float(since_epoch) if since_epoch is not None else None
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix not in IMAGE_SUFFIXES and suffix not in DOCUMENT_SUFFIXES:
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if since is not None and mtime < (since - 2.0):
                continue
            if suffix in IMAGE_SUFFIXES:
                images.append((mtime, path))
            else:
                documents.append((mtime, path))
    images.sort(key=lambda item: item[0], reverse=True)
    documents.sort(key=lambda item: item[0], reverse=True)
    return (
        [path for _, path in images[:max_images]],
        [path for _, path in documents[:max_documents]],
    )


def _build_run_summary_message(
    *,
    mode: str,
    workspace: dict[str, Any],
    repo_dir: Path,
    code: int,
    outcome: dict[str, Any] | None,
) -> str:
    status = str((outcome or {}).get("status") or "").strip()
    reason = str((outcome or {}).get("reason") or "").strip().replace("_", " ")
    provider_exit_code = (outcome or {}).get("provider_exit_code")

    effective_mode = str(mode or "loop").strip().lower()
    if effective_mode not in SUPPORTED_GATEWAY_RUN_MODES:
        effective_mode = "loop"

    lines = [
        f"Run complete in workspace <code>{_html_escape(workspace['label'])}</code>.",
        f"Execution mode: <code>{_html_escape(effective_mode)}</code>.",
    ]
    if effective_mode == "loop":
        if code == 0 and status in {"", "done"}:
            lines.append("The requested simulation workflow finished successfully.")
        elif status == "incomplete_max_iterations":
            lines.append(
                "The run stopped before completion because max iterations were reached."
            )
        elif status == "provider_failure":
            if isinstance(provider_exit_code, int):
                lines.append(
                    f"The run failed with provider exit code {provider_exit_code}."
                )
            else:
                lines.append("The run failed due to a provider/runtime error.")
        else:
            lines.append(f"The run exited with status code {code}.")
    elif effective_mode == "exec":
        if code == 0:
            lines.append("Single-turn execution finished successfully.")
        elif isinstance(provider_exit_code, int):
            lines.append(
                f"Execution failed with provider exit code {provider_exit_code}."
            )
        else:
            lines.append(f"Execution failed with status code {code}.")
    else:
        workflow_label = (
            "Research workflow"
            if effective_mode == "research"
            else "Reproduce workflow"
        )
        if code == 0 and status in {"", "done"}:
            lines.append(f"{workflow_label} orchestration finished successfully.")
        elif isinstance(provider_exit_code, int):
            lines.append(
                f"{workflow_label} failed with provider exit code {provider_exit_code}."
            )
        else:
            lines.append(f"{workflow_label} failed with status code {code}.")

    if reason and not (
        reason == "done token" and effective_mode == "loop" and code == 0
    ):
        lines.append(f"Reason: {_html_escape(reason)}.")

    memory_path = repo_dir / "projects" / "memory.md"
    memory_text = ""
    key_results: list[str] = []
    done_steps: list[str] = []
    pending_steps: list[str] = []
    parameter_source_items: list[str] = []
    simulation_uncertainty_items: list[str] = []
    try:
        if memory_path.is_file():
            memory_text = memory_path.read_text(encoding="utf-8")
            key_results = _extract_key_results(memory_text, max_items=20)
            done_steps, pending_steps = _extract_plan_progress(memory_text)
            parameter_source_items = _extract_memory_section_items(
                memory_text,
                heading="### Parameter source mapping",
                placeholder_markers=("run_id", "parameter_or_setting", "source"),
                max_items=8,
            )
            simulation_uncertainty_items = _extract_memory_section_items(
                memory_text,
                heading="### Simulation uncertainty",
                placeholder_markers=(
                    "run_id",
                    "uncertainty_or_assumption",
                    "mitigation_or_next_step",
                ),
                max_items=8,
            )
    except OSError:
        key_results = []
        done_steps = []
        pending_steps = []
        parameter_source_items = []
        simulation_uncertainty_items = []

    summary_key_results = _select_key_results_for_summary(key_results, max_items=1)
    parameter_source_items = _filter_items_to_latest_run_id(
        parameter_source_items,
        run_id_resolver=_parameter_source_run_id,
    )
    simulation_uncertainty_items = _filter_items_to_latest_run_id(
        simulation_uncertainty_items,
        run_id_resolver=_simulation_uncertainty_run_id,
    )

    if done_steps:
        lines.append("")
        lines.append("<b>What Was Done</b>")
        for item in done_steps:
            lines.append(f"• {_html_escape(item)}")

    if summary_key_results:
        lines.append("")
        lines.append("<b>Key Findings</b>")
        for headline, detail in _format_key_results_human(summary_key_results):
            lines.append(f"• {_html_escape(headline)}")
            if detail:
                lines.append(f"  <i>{_html_escape(detail)}</i>")
    else:
        lines.append("")
        lines.append("<b>Key Findings</b>")
        lines.append(
            "• Key findings are not recorded yet in <code>projects/memory.md</code>."
        )

    lines.append("")
    lines.append("<b>Parameter Source Mapping</b>")
    if parameter_source_items:
        for headline, detail in _format_parameter_source_mapping_human(
            parameter_source_items
        ):
            lines.append(f"• {_html_escape(headline)}")
            if detail:
                lines.append(f"  <i>{_html_escape(detail)}</i>")
    else:
        lines.append(
            "• Parameter provenance is not recorded yet in <code>projects/memory.md</code>."
        )

    lines.append("")
    lines.append("<b>Simulation Uncertainty</b>")
    if simulation_uncertainty_items:
        for headline, detail in _format_simulation_uncertainty_human(
            simulation_uncertainty_items
        ):
            lines.append(f"• {_html_escape(headline)}")
            if detail:
                lines.append(f"  <i>{_html_escape(detail)}</i>")
    else:
        lines.append(
            "• Uncertainty notes are not recorded yet in <code>projects/memory.md</code>."
        )

    if pending_steps:
        lines.append("")
        lines.append("<b>Still Pending</b>")
        for item in pending_steps:
            lines.append(f"• {_html_escape(item)}")

    recent_artifacts = _collect_recent_artifacts(repo_dir)
    if recent_artifacts:
        lines.append("")
        lines.append("<b>Recent Artifacts</b>")
        for rel in recent_artifacts:
            lines.append(f"• <code>{_html_escape(rel)}</code>")

    lines.append("")
    lines.append(
        "Commands: <code>/new</code>, <code>/use</code>, <code>/mode</code>, "
        "<code>/loopcfg</code>, <code>/reply</code>, <code>/where</code>, "
        "<code>/list</code>"
    )
    message = "\n".join(lines)
    if len(message) > 4096:
        # Keep full summary well under Telegram hard limit.
        message = "\n".join(lines[:22])
    return message


def _build_status_message(
    chat_state: dict[str, Any],
    *,
    repo_dir: Path | None = None,
) -> str:
    mode = _effective_execution_mode(chat_state)
    active = _active_workspace(chat_state)
    active_workspace_text = (
        str(active.get("label") or "").strip() if active is not None else ""
    )
    if not active_workspace_text:
        active_workspace_text = "(none yet)"
    is_running = bool(chat_state.get("is_running"))
    pending_raw = chat_state.get("pending_run_count")
    if isinstance(pending_raw, int):
        pending_count = max(0, pending_raw)
    else:
        pending_count = 0
    now_text = _format_local_timestamp(_now_utc_iso())

    if is_running and pending_count > 0:
        run_state_text = f"<b>running</b> ({pending_count} queued)"
    elif is_running:
        run_state_text = "<b>running</b>"
    elif pending_count > 0:
        run_state_text = f"<b>queued</b> ({pending_count} pending)"
    else:
        run_state_text = "<b>idle</b>"

    current_run_id = str(chat_state.get("current_run_id") or "").strip()
    current_run_started = str(
        chat_state.get("current_run_started_at_utc") or ""
    ).strip()
    current_run_mode = str(chat_state.get("current_run_mode") or "").strip()
    current_run_prompt_preview = str(
        chat_state.get("current_run_prompt_preview") or ""
    ).strip()
    mode_text = current_run_mode if is_running and current_run_mode else mode

    lines = [
        "<b>Gateway Status</b>",
        f"• State: <b>online</b> (responding now at <code>{_html_escape(now_text)}</code>)",
        f"• Agent: {run_state_text}",
        f"• Mode: <code>{_html_escape(mode_text)}</code>",
        f"• Active workspace: <code>{_html_escape(active_workspace_text)}</code>",
    ]

    if is_running:
        lines.append("")
        lines.append("<b>Current Run</b>")
        if current_run_started:
            started_local = _format_local_timestamp(current_run_started)
            lines.append(f"• Started: <code>{_html_escape(started_local)}</code>")
        if current_run_id:
            lines.append(f"• Job id: <code>{_html_escape(current_run_id)}</code>")
        if current_run_prompt_preview:
            lines.append(f"• Prompt: {_html_escape(current_run_prompt_preview)}")

    last_run_mode = str(chat_state.get("last_run_mode") or "").strip().lower()
    last_run_started = str(chat_state.get("last_run_started_at_utc") or "").strip()
    last_run_finished = str(chat_state.get("last_run_finished_at_utc") or "").strip()
    last_run_status = str(chat_state.get("last_run_status") or "").strip()
    last_run_reason = (
        str(chat_state.get("last_run_reason") or "").strip().replace("_", " ")
    )
    last_run_exit_code = chat_state.get("last_run_exit_code")

    if not is_running:
        if last_run_status:
            lines.append("")
            lines.append("<b>Last Run</b>")
            if last_run_mode:
                lines.append(f"• Mode: <code>{_html_escape(last_run_mode)}</code>")
            if last_run_started:
                started_local = _format_local_timestamp(last_run_started)
                lines.append(f"• Started: <code>{_html_escape(started_local)}</code>")
            if last_run_finished:
                finished_local = _format_local_timestamp(last_run_finished)
                lines.append(f"• Finished: <code>{_html_escape(finished_local)}</code>")
            lines.append(f"• Status: <code>{_html_escape(last_run_status)}</code>")
            if isinstance(last_run_exit_code, int):
                lines.append(f"• Exit code: <code>{last_run_exit_code}</code>")
            if last_run_reason:
                lines.append(f"• Reason: {_html_escape(last_run_reason)}")
        else:
            lines.append("")
            lines.append("<b>Last Run</b>")
            lines.append("• No completed run recorded yet for this chat.")

    lines.append("")
    lines.append(
        "Commands: <code>/mode</code>, <code>/loopcfg</code>, <code>/reply</code>, "
        "<code>/new</code>, <code>/use</code>, <code>/where</code>, "
        "<code>/list</code>"
    )
    message = "\n".join(lines)
    if len(message) > 4096:
        return _truncate_message(_strip_html_tags(message), limit=3900)
    return message


def _resolve_run_mode(
    chat_state: dict[str, Any], requested_mode: str | None = None
) -> str:
    if requested_mode:
        mode = str(requested_mode).strip().lower()
        if mode in SUPPORTED_GATEWAY_RUN_MODES:
            return mode
    return _effective_execution_mode(chat_state)


def _derive_run_outcome(
    code: int,
    outcome: dict[str, Any] | None,
) -> tuple[str, str, int | None]:
    status = str((outcome or {}).get("status") or "").strip() or (
        "done" if int(code) == 0 else "provider_failure"
    )
    reason = str((outcome or {}).get("reason") or "").strip() or (
        "completed" if int(code) == 0 else f"provider_exit_code_{int(code)}"
    )
    provider_exit_code_raw = (outcome or {}).get("provider_exit_code")
    provider_exit_code: int | None
    if isinstance(provider_exit_code_raw, int):
        provider_exit_code = provider_exit_code_raw
    else:
        provider_exit_code = int(code) if int(code) != 0 else None
    return status, reason, provider_exit_code


def _run_prompt_for_workspace(
    *,
    chat_state: dict[str, Any],
    workspace: dict[str, Any],
    prompt: str,
    requested_mode: str | None,
    workspaces_root: Path,
    loop_config: GatewayLoopConfig,
    loop_runner: LoopRunner | None = None,
    exec_runner: ExecRunner | None = None,
    research_runner: WorkflowRunner | None = None,
    reproduce_runner: WorkflowRunner | None = None,
    workspace_repo_ensurer: WorkspaceRepoEnsurer | None = None,
) -> tuple[str, Path, float]:
    _touch_workspace(workspace)
    repo_dir = workspaces_root / str(workspace["id"]) / "repo"
    repo_ensurer = workspace_repo_ensurer or _ensure_workspace_repo
    repo_ensurer(repo_dir, loop_config.init_git)

    mode = _resolve_run_mode(chat_state, requested_mode=requested_mode)
    run_started_epoch = time.time()
    chat_state["last_run_started_at_utc"] = _now_utc_iso()
    chat_state["last_run_mode"] = mode
    code, outcome = _run_prompt_with_mode(
        repo_dir=repo_dir,
        prompt=prompt,
        mode=mode,
        loop_config=loop_config,
        loop_runner=loop_runner,
        exec_runner=exec_runner,
        research_runner=research_runner,
        reproduce_runner=reproduce_runner,
    )
    status, reason, provider_exit_code = _derive_run_outcome(code, outcome)
    chat_state["last_run_status"] = status
    chat_state["last_run_reason"] = reason
    chat_state["last_run_exit_code"] = provider_exit_code
    chat_state["last_run_finished_at_utc"] = _now_utc_iso()
    _record_last_run_agent_reply(chat_state, outcome)
    summary = _build_run_summary_message(
        mode=mode,
        workspace=workspace,
        repo_dir=repo_dir,
        code=code,
        outcome=outcome,
    )
    reply_style = _effective_reply_style(chat_state)
    final_reply = _compose_run_completion_message(
        summary=summary,
        outcome=outcome,
        reply_style=reply_style,
    )
    return final_reply, repo_dir, run_started_epoch


def _run_prompt_with_mode(
    *,
    repo_dir: Path,
    prompt: str,
    mode: str,
    loop_config: GatewayLoopConfig,
    loop_runner: LoopRunner | None = None,
    exec_runner: ExecRunner | None = None,
    research_runner: WorkflowRunner | None = None,
    reproduce_runner: WorkflowRunner | None = None,
) -> tuple[int, dict[str, Any] | None]:
    if mode == "exec":
        runner = exec_runner or _run_exec_in_workspace
    elif mode == "loop":
        runner = loop_runner or _run_loop_in_workspace
    elif mode == "research":
        runner = research_runner or _run_research_in_workspace
    elif mode == "reproduce":
        runner = reproduce_runner or _run_reproduce_in_workspace
    else:
        runner = loop_runner or _run_loop_in_workspace
    return runner(repo_dir, prompt, loop_config)


def _queue_telegram_run(
    *,
    text: str,
    chat_id: str,
    chat_key: str,
    state: dict[str, Any],
    loop_config: GatewayLoopConfig | None = None,
) -> tuple[QueuedRunJob, str]:
    telegram = _telegram_state(state)
    chat_state = _ensure_chat_state(telegram, chat_key)
    base_loop_config = loop_config or _default_gateway_loop_config()
    effective_loop_config = _effective_loop_config(
        chat_state, base_loop_config=base_loop_config
    )
    workspace = _ensure_active_workspace(chat_state, chat_id=chat_id)
    _touch_workspace(workspace)
    mode, prompt = _resolve_prompt_mode_and_text(chat_state=chat_state, text=text)
    pending_raw = chat_state.get("pending_run_count")
    pending_count = max(0, int(pending_raw)) if isinstance(pending_raw, int) else 0
    chat_state["pending_run_count"] = pending_count + 1
    queue_position = pending_count + 1
    queued_at = _now_utc_iso()
    job = QueuedRunJob(
        job_id=f"job-{uuid.uuid4().hex[:10]}",
        chat_id=chat_id,
        chat_key=chat_key,
        prompt=prompt,
        mode=mode,
        workspace_id=str(workspace["id"]),
        workspace_label=str(workspace["label"]),
        max_iterations=effective_loop_config.max_iterations,
        max_wait_seconds=effective_loop_config.max_wait_seconds,
        queued_at_utc=queued_at,
    )

    if bool(chat_state.get("is_running")) or pending_count > 0:
        reply = (
            f"Queued request in workspace <code>{_html_escape(workspace['label'])}</code>.\n"
            f"Execution mode: <code>{_html_escape(mode)}</code>.\n"
            f"Loop controls: <code>--max-iterations={job.max_iterations}, "
            f"--max-wait-seconds={_format_loop_control_number(job.max_wait_seconds)}</code>.\n"
            f"Queue position: <code>{queue_position}</code>.\n"
            "Use <code>/status</code> to monitor progress."
        )
    else:
        reply = (
            f"Request accepted in workspace <code>{_html_escape(workspace['label'])}</code>.\n"
            f"Execution mode: <code>{_html_escape(mode)}</code>.\n"
            f"Loop controls: <code>--max-iterations={job.max_iterations}, "
            f"--max-wait-seconds={_format_loop_control_number(job.max_wait_seconds)}</code>.\n"
            "Run queued and starting shortly.\n"
            "Use <code>/status</code> to monitor progress."
        )
    return job, reply


def _mark_chat_job_running(chat_state: dict[str, Any], job: QueuedRunJob) -> None:
    pending_raw = chat_state.get("pending_run_count")
    pending_count = max(0, int(pending_raw)) if isinstance(pending_raw, int) else 0
    chat_state["pending_run_count"] = max(0, pending_count - 1)
    chat_state["is_running"] = True
    chat_state["current_run_id"] = job.job_id
    chat_state["current_run_started_at_utc"] = _now_utc_iso()
    chat_state["current_run_mode"] = job.mode
    chat_state["current_run_workspace_id"] = job.workspace_id
    chat_state["current_run_workspace_label"] = job.workspace_label
    chat_state["current_run_prompt_preview"] = _normalize_prompt_preview(job.prompt)


def _mark_chat_job_idle(chat_state: dict[str, Any]) -> None:
    chat_state["is_running"] = False
    chat_state["current_run_id"] = ""
    chat_state["current_run_started_at_utc"] = ""
    chat_state["current_run_mode"] = ""
    chat_state["current_run_workspace_id"] = ""
    chat_state["current_run_workspace_label"] = ""
    chat_state["current_run_prompt_preview"] = ""


def _handle_telegram_text(
    *,
    text: str,
    chat_id: str,
    chat_key: str,
    state: dict[str, Any],
    workspaces_root: Path,
    loop_config: GatewayLoopConfig,
    loop_runner: LoopRunner | None = None,
    exec_runner: ExecRunner | None = None,
    research_runner: WorkflowRunner | None = None,
    reproduce_runner: WorkflowRunner | None = None,
    workspace_repo_ensurer: WorkspaceRepoEnsurer | None = None,
) -> str:
    telegram = _telegram_state(state)
    chat_state = _ensure_chat_state(telegram, chat_key)
    command, argument = _parse_gateway_command(text)

    if command in {"/help", "/start"}:
        return GATEWAY_HELP_TEXT

    if command == "/new":
        workspace = _create_workspace(
            chat_state,
            chat_id=chat_id,
            requested_label=argument or "main",
            created_via="new",
        )
        return (
            f"Switched to new workspace: {_format_workspace_short(workspace)}\n"
            f"{GATEWAY_HELP_TEXT}"
        )

    if command == "/use":
        if not argument:
            return "Usage: /use <name-or-id>"
        workspace = _find_workspace(chat_state, argument)
        if workspace is None:
            return (
                f"Workspace not found: {argument}\n{_format_workspace_list(chat_state)}"
            )
        _set_active_workspace(chat_state, str(workspace["id"]))
        return f"Switched workspace: {_format_workspace_short(workspace)}"

    if command == "/mode":
        current_mode = _effective_execution_mode(chat_state)

        if not argument:
            return (
                f"Current mode: {current_mode}\n"
                "Usage: /mode <exec|loop|research|reproduce>\n"
                "Normal messages run with this mode in the active workspace."
            )

        requested = str(argument).split()[0].strip().lower()
        if requested not in SUPPORTED_GATEWAY_RUN_MODES:
            return (
                f"Unsupported mode: {requested}\n"
                "Usage: /mode <exec|loop|research|reproduce>"
            )

        chat_state["execution_mode"] = requested
        if requested == "exec":
            return (
                "Execution mode set to exec.\n"
                "Normal messages will run with `fermilink exec`."
            )
        if requested == "loop":
            return (
                "Execution mode set to loop.\n"
                "Normal messages will run with `fermilink loop`."
            )
        if requested == "research":
            return (
                "Execution mode set to research.\n"
                "Normal messages will run with `fermilink research`."
            )
        return (
            "Execution mode set to reproduce.\n"
            "Normal messages will run with `fermilink reproduce`."
        )

    if command == "/loopcfg":
        if not argument:
            return _build_loopcfg_message(chat_state, base_loop_config=loop_config)
        max_iterations, max_wait_seconds, reset, error = _parse_loopcfg_updates(
            argument
        )
        if error:
            return (
                f"{error}\n"
                "Usage: /loopcfg [--max-iterations N] "
                "[--max-wait-seconds S] [--reset]"
            )
        if reset:
            chat_state["loop_max_iterations_override"] = None
            chat_state["loop_max_wait_seconds_override"] = None
            return (
                "Loop controls reset to gateway defaults.\n"
                f"{_build_loopcfg_message(chat_state, base_loop_config=loop_config)}"
            )
        if max_iterations is not None:
            chat_state["loop_max_iterations_override"] = max_iterations
        if max_wait_seconds is not None:
            chat_state["loop_max_wait_seconds_override"] = max_wait_seconds
        return (
            "Loop controls updated for this chat.\n"
            f"{_build_loopcfg_message(chat_state, base_loop_config=loop_config)}"
        )

    if command == "/reply":
        current_style = _effective_reply_style(chat_state)

        if not argument:
            return (
                f"Current reply style: {current_style}\n"
                "Usage: /reply <summary|agent|both>\n"
                "Normal message completion replies use this style."
            )

        requested_style = str(argument).split()[0].strip().lower()
        if requested_style not in SUPPORTED_REPLY_STYLES:
            return (
                f"Unsupported reply style: {requested_style}\n"
                "Usage: /reply <summary|agent|both>"
            )
        chat_state["reply_style"] = requested_style
        return (
            f"Reply style set to {requested_style}.\n"
            "Normal message completion replies will use this style."
        )

    if command == "/status":
        _, status_repo_dir = _resolve_active_workspace_repo(
            state=state,
            chat_key=chat_key,
            workspaces_root=workspaces_root,
        )
        return _build_status_message(chat_state, repo_dir=status_repo_dir)

    if command == "/list":
        return _format_workspace_list(chat_state)

    if command == "/where":
        workspace = _ensure_active_workspace(chat_state, chat_id=chat_id)
        mode = _effective_execution_mode(chat_state)
        return (
            f"Active workspace: {_format_workspace_short(workspace)}\n"
            f"Current mode: {mode}"
        )

    requested_mode, run_prompt = _resolve_prompt_mode_and_text(
        chat_state=chat_state, text=text
    )
    workspace = _ensure_active_workspace(chat_state, chat_id=chat_id)
    effective_loop_config = _effective_loop_config(
        chat_state, base_loop_config=loop_config
    )
    reply, _, _ = _run_prompt_for_workspace(
        chat_state=chat_state,
        workspace=workspace,
        prompt=run_prompt,
        requested_mode=requested_mode,
        workspaces_root=workspaces_root,
        loop_config=effective_loop_config,
        loop_runner=loop_runner,
        exec_runner=exec_runner,
        research_runner=research_runner,
        reproduce_runner=reproduce_runner,
        workspace_repo_ensurer=workspace_repo_ensurer,
    )
    return reply


def _resolve_active_workspace_repo(
    *,
    state: dict[str, Any],
    chat_key: str,
    workspaces_root: Path,
) -> tuple[dict[str, Any] | None, Path | None]:
    telegram = _telegram_state(state)
    chat_state = _ensure_chat_state(telegram, chat_key)
    workspace = _active_workspace(chat_state)
    if workspace is None:
        return None, None
    repo_dir = workspaces_root / str(workspace["id"]) / "repo"
    return workspace, repo_dir


def _load_memory_text(repo_dir: Path) -> str:
    memory_path = repo_dir / "projects" / "memory.md"
    if not memory_path.is_file():
        return ""
    try:
        return memory_path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _load_memory_key_results(repo_dir: Path) -> list[str]:
    memory_text = _load_memory_text(repo_dir)
    if not memory_text:
        return []
    return _extract_key_results(memory_text)


def _collect_media_for_run_reply(
    repo_dir: Path,
    *,
    run_started_epoch: float | None,
) -> tuple[list[Path], list[Path]]:
    key_results = _load_memory_key_results(repo_dir)
    key_media = _collect_key_result_media(repo_dir, key_results)
    key_images = [path for path in key_media if path.suffix.lower() in IMAGE_SUFFIXES]
    key_docs = [path for path in key_media if path.suffix.lower() in DOCUMENT_SUFFIXES]

    recent_images, recent_docs = _collect_recent_media(
        repo_dir,
        since_epoch=run_started_epoch,
    )
    if not recent_images and not recent_docs:
        recent_images, recent_docs = _collect_recent_media(
            repo_dir,
            since_epoch=None,
            max_images=1,
            max_documents=1,
        )

    images: list[Path] = []
    docs: list[Path] = []
    seen: set[Path] = set()
    for path in [*key_images, *recent_images]:
        if path in seen:
            continue
        seen.add(path)
        images.append(path)
        if len(images) >= 3:
            break
    for path in [*key_docs, *recent_docs]:
        if path in seen:
            continue
        seen.add(path)
        docs.append(path)
        if len(docs) >= 2:
            break
    return images, docs


def _send_run_media_reply(
    *,
    client: _TelegramApiClient,
    chat_id: str,
    workspace: dict[str, Any] | None,
    repo_dir: Path | None,
    run_started_epoch: float | None,
    on_error: Callable[[str], None],
) -> None:
    if workspace is None or repo_dir is None or not repo_dir.is_dir():
        return
    images, documents = _collect_media_for_run_reply(
        repo_dir,
        run_started_epoch=run_started_epoch,
    )
    if not images and not documents:
        return
    workspace_label = str(workspace.get("label") or "workspace")
    for index, image_path in enumerate(images):
        caption = None
        if index == 0:
            caption = f"Generated figure(s) from workspace {workspace_label}"
        try:
            client.send_photo(chat_id=chat_id, file_path=image_path, caption=caption)
        except Exception as exc:  # pragma: no cover - network errors
            on_error(f"failed to send photo {image_path}: {exc}")
    for index, doc_path in enumerate(documents):
        caption = None
        if index == 0 and not images:
            caption = f"Generated document(s) from workspace {workspace_label}"
        try:
            client.send_document(chat_id=chat_id, file_path=doc_path, caption=caption)
        except Exception as exc:  # pragma: no cover - network errors
            on_error(f"failed to send document {doc_path}: {exc}")


def cmd_gateway(args: argparse.Namespace) -> int:
    """
    Execute the `gateway` CLI subcommand.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed CLI arguments namespace for the subcommand.

    Returns
    -------
    int
        Process exit code (`0` on user stop).
    """

    cli = _cli()
    token = str(
        (
            getattr(args, "telegram_token", None)
            or os.getenv("FERMILINK_GATEWAY_TELEGRAM_TOKEN")
            or ""
        )
    ).strip()
    if not token:
        raise cli.PackageError(
            "Telegram token is required. Set --telegram-token or "
            "FERMILINK_GATEWAY_TELEGRAM_TOKEN."
        )

    poll_timeout_seconds = int(getattr(args, "poll_timeout_seconds", 30))
    if poll_timeout_seconds < 1:
        raise cli.PackageError("--poll-timeout-seconds must be >= 1.")

    loop_config = _build_loop_config(args)
    allow_from = _parse_allow_from(getattr(args, "allow_from", None))
    session_store_path = _resolve_session_store_path(
        getattr(args, "session_store", None)
    )
    state = _load_gateway_state(session_store_path)
    telegram = _telegram_state(state)
    offset = int(telegram.get("offset") or 0)
    workspaces_root = resolve_workspaces_root()
    state_lock = threading.Lock()
    send_lock = threading.Lock()
    run_queue: queue.Queue[QueuedRunJob | None] = queue.Queue()
    worker_stop = threading.Event()

    chats_raw = telegram.get("chats")
    if isinstance(chats_raw, dict):
        for key in list(chats_raw.keys()):
            chat_state = _ensure_chat_state(telegram, str(key))
            _clear_chat_runtime_status(chat_state)
    _save_gateway_state(session_store_path, state)

    client = _TelegramApiClient(token=token)

    def _send_message_safe(
        *, chat_id: str, text: str, parse_mode: str | None = "HTML"
    ) -> None:
        with send_lock:
            client.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)

    def _run_worker() -> None:
        while not worker_stop.is_set():
            try:
                job = run_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if job is None:
                run_queue.task_done()
                return

            mode = job.mode if job.mode in SUPPORTED_GATEWAY_RUN_MODES else "loop"
            workspace: dict[str, Any] = {
                "id": job.workspace_id,
                "label": job.workspace_label,
            }
            repo_dir = workspaces_root / job.workspace_id / "repo"
            run_started_epoch = time.time()

            with state_lock:
                telegram_local = _telegram_state(state)
                chat_state = _ensure_chat_state(telegram_local, job.chat_key)
                workspace_record = _workspace_by_id(chat_state, job.workspace_id)
                if workspace_record is not None:
                    workspace = workspace_record
                _mark_chat_job_running(chat_state, job)
                chat_state["last_run_started_at_utc"] = _now_utc_iso()
                chat_state["last_run_mode"] = mode
                _save_gateway_state(session_store_path, state)

            summary = ""
            final_reply = ""
            reply_style = "agent"
            try:
                _ensure_workspace_repo(repo_dir, loop_config.init_git)
                run_loop_config = replace(
                    loop_config,
                    max_iterations=job.max_iterations,
                    max_wait_seconds=job.max_wait_seconds,
                )
                if mode == "loop":

                    def _iteration_hook(iteration: int, max_iterations: int) -> None:
                        progress_mode = f"loop {iteration}/{max_iterations}"
                        with state_lock:
                            telegram_local = _telegram_state(state)
                            chat_state_local = _ensure_chat_state(
                                telegram_local, job.chat_key
                            )
                            if not bool(chat_state_local.get("is_running")):
                                return
                            chat_state_local["current_run_mode"] = progress_mode
                            _save_gateway_state(session_store_path, state)

                    run_loop_config = replace(
                        run_loop_config,
                        loop_iteration_hook=_iteration_hook,
                    )
                code, outcome = _run_prompt_with_mode(
                    repo_dir=repo_dir,
                    prompt=job.prompt,
                    mode=mode,
                    loop_config=run_loop_config,
                    loop_runner=None,
                    exec_runner=None,
                )
                status, reason, provider_exit_code = _derive_run_outcome(code, outcome)
                with state_lock:
                    telegram_local = _telegram_state(state)
                    chat_state = _ensure_chat_state(telegram_local, job.chat_key)
                    workspace_record = _workspace_by_id(chat_state, job.workspace_id)
                    if workspace_record is not None:
                        workspace = workspace_record
                    chat_state["last_run_status"] = status
                    chat_state["last_run_reason"] = reason
                    chat_state["last_run_exit_code"] = provider_exit_code
                    chat_state["last_run_finished_at_utc"] = _now_utc_iso()
                    _record_last_run_agent_reply(chat_state, outcome)
                    reply_style = _effective_reply_style(chat_state)
                    _mark_chat_job_idle(chat_state)
                    _save_gateway_state(session_store_path, state)
                summary = _build_run_summary_message(
                    mode=mode,
                    workspace=workspace,
                    repo_dir=repo_dir,
                    code=code,
                    outcome=outcome,
                )
                final_reply = _compose_run_completion_message(
                    summary=summary,
                    outcome=outcome,
                    reply_style=reply_style,
                )
            except Exception as exc:
                cli._print_tagged(
                    "gateway",
                    f"queued run failed for {job.chat_key}: {exc}",
                    stderr=True,
                )
                with state_lock:
                    telegram_local = _telegram_state(state)
                    chat_state = _ensure_chat_state(telegram_local, job.chat_key)
                    chat_state["last_run_status"] = "provider_failure"
                    chat_state["last_run_reason"] = (
                        f"gateway_error_{type(exc).__name__}"
                    )
                    chat_state["last_run_exit_code"] = None
                    chat_state["last_run_finished_at_utc"] = _now_utc_iso()
                    _record_last_run_agent_reply(chat_state, None)
                    _mark_chat_job_idle(chat_state)
                    _save_gateway_state(session_store_path, state)
                summary = f"Gateway error: {exc}"
                final_reply = summary

            if final_reply:
                try:
                    _send_message_safe(
                        chat_id=job.chat_id,
                        text=final_reply,
                        parse_mode="HTML",
                    )
                except Exception as exc:  # pragma: no cover - network errors
                    cli._print_tagged(
                        "gateway",
                        f"failed to send queued-run reply to {job.chat_key}: {exc}",
                        stderr=True,
                    )

            with send_lock:
                _send_run_media_reply(
                    client=client,
                    chat_id=job.chat_id,
                    workspace=workspace,
                    repo_dir=repo_dir,
                    run_started_epoch=run_started_epoch,
                    on_error=lambda msg: cli._print_tagged("gateway", msg, stderr=True),
                )
            run_queue.task_done()

    worker_thread = threading.Thread(
        target=_run_worker,
        name="fermilink-gateway-worker",
        daemon=True,
    )
    worker_thread.start()

    cli._print_tagged("gateway", f"session store: {session_store_path}")
    cli._print_tagged("gateway", f"workspaces root: {workspaces_root}")
    if allow_from:
        preview = ", ".join(sorted(allow_from))
        cli._print_tagged("gateway", f"telegram allowlist: {preview}")
    else:
        cli._print_tagged(
            "gateway", "telegram allowlist: disabled (all senders allowed)"
        )
    cli._print_tagged("gateway", "running (Ctrl-C to stop)")

    try:
        while True:
            try:
                updates = client.get_updates(
                    offset=offset,
                    timeout_seconds=poll_timeout_seconds,
                )
            except (httpx.HTTPError, RuntimeError) as exc:
                cli._print_tagged(
                    "gateway",
                    f"telegram polling error: {exc}; retrying in 3s.",
                    stderr=True,
                )
                time.sleep(3.0)
                continue

            if not updates:
                continue

            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = max(offset, update_id + 1)
                    with state_lock:
                        telegram["offset"] = offset

                message = update.get("message")
                if not isinstance(message, dict):
                    with state_lock:
                        _save_gateway_state(session_store_path, state)
                    continue

                chat_info = message.get("chat")
                sender_info = message.get("from")
                text = message.get("text")
                if not isinstance(chat_info, dict):
                    with state_lock:
                        _save_gateway_state(session_store_path, state)
                    continue
                chat_id_raw = chat_info.get("id")
                if chat_id_raw is None:
                    with state_lock:
                        _save_gateway_state(session_store_path, state)
                    continue
                chat_id = str(chat_id_raw)
                chat_key = f"telegram:{chat_id}"

                sender_id = ""
                sender_username: str | None = None
                if isinstance(sender_info, dict):
                    sender_id = str(sender_info.get("id") or "")
                    username_raw = sender_info.get("username")
                    if isinstance(username_raw, str) and username_raw.strip():
                        sender_username = username_raw.strip()

                if not _is_sender_allowed(
                    sender_id=sender_id,
                    username=sender_username,
                    allowed_tokens=allow_from,
                ):
                    with state_lock:
                        _save_gateway_state(session_store_path, state)
                    try:
                        _send_message_safe(
                            chat_id=chat_id,
                            text="Access denied.",
                            parse_mode=None,
                        )
                    except Exception as exc:  # pragma: no cover - network errors
                        cli._print_tagged(
                            "gateway",
                            f"failed to send access-denied notice: {exc}",
                            stderr=True,
                        )
                    continue

                if not isinstance(text, str) or not text.strip():
                    with state_lock:
                        _save_gateway_state(session_store_path, state)
                    continue

                cli._print_tagged(
                    "gateway",
                    f"received message for {chat_key}: {text[:120]!r}",
                )
                command, _ = _parse_gateway_command(text)
                if command is None:
                    try:
                        with state_lock:
                            job, reply = _queue_telegram_run(
                                text=text,
                                chat_id=chat_id,
                                chat_key=chat_key,
                                state=state,
                                loop_config=loop_config,
                            )
                            _save_gateway_state(session_store_path, state)
                        run_queue.put(job)
                    except Exception as exc:
                        cli._print_tagged(
                            "gateway",
                            f"message queueing failed for {chat_key}: {exc}",
                            stderr=True,
                        )
                        reply = f"Gateway error: {exc}"
                else:
                    try:
                        with state_lock:
                            reply = _handle_telegram_text(
                                text=text,
                                chat_id=chat_id,
                                chat_key=chat_key,
                                state=state,
                                workspaces_root=workspaces_root,
                                loop_config=loop_config,
                            )
                            _save_gateway_state(session_store_path, state)
                    except Exception as exc:
                        cli._print_tagged(
                            "gateway",
                            f"message handling failed for {chat_key}: {exc}",
                            stderr=True,
                        )
                        reply = f"Gateway error: {exc}"

                if not reply:
                    continue
                try:
                    _send_message_safe(chat_id=chat_id, text=reply, parse_mode="HTML")
                except Exception as exc:  # pragma: no cover - network errors
                    cli._print_tagged(
                        "gateway",
                        f"failed to send message to {chat_key}: {exc}",
                        stderr=True,
                    )
                    continue
    except KeyboardInterrupt:
        worker_stop.set()
        run_queue.put(None)
        with state_lock:
            telegram = _telegram_state(state)
            chats = telegram.get("chats")
            if isinstance(chats, dict):
                for key in list(chats.keys()):
                    chat_state = _ensure_chat_state(telegram, str(key))
                    _clear_chat_runtime_status(chat_state)
            _save_gateway_state(session_store_path, state)
        cli._print_tagged("gateway", "stopped.")
        return 0
    finally:
        worker_stop.set()
        run_queue.put(None)
        worker_thread.join(timeout=1.0)
        client.close()
