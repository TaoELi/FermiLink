import asyncio
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from passlib.hash import pbkdf2_sha256
from fermilink.agent_runtime import resolve_agent_runtime_policy
from fermilink.cli.workflow_prompts import UNIFIED_MEMORY_PROMPT_PREFIX
from fermilink.config import (
    resolve_fermilink_home,
    resolve_workspaces_root as resolve_default_workspaces_root,
)
from fermilink.runner.scientific_packages import (
    load_registry,
    normalize_package_id,
    resolve_scipkg_root,
)
from fermilink.web import (
    activity_helpers,
    artifact_helpers,
    auth_helpers,
    chat_helpers,
    package_router_helpers,
    package_session_helpers,
    runner_helpers,
    sqlite_helpers,
    status_helpers,
    storage_helpers,
)


app_root_raw = os.getenv("FERMILINK_CHAINLIT_APP_ROOT")
if not app_root_raw or not app_root_raw.strip():
    app_root_raw = str(resolve_fermilink_home())
APP_ROOT = Path(app_root_raw).expanduser()
if not APP_ROOT.is_absolute():
    APP_ROOT = (Path.cwd() / APP_ROOT).resolve()
PACKAGE_PUBLIC_ROOT = Path(__file__).resolve().parents[1] / "public"
PACKAGED_CHAINLIT_MARKDOWN = PACKAGE_PUBLIC_ROOT / "fermilink.md"
TARGET_CHAINLIT_MARKDOWN = APP_ROOT / "chainlit.md"
DEFAULT_DB_PATH = APP_ROOT / ".chainlit" / "chainlit.db"
DEFAULT_DB_URL = f"sqlite+aiosqlite:///{DEFAULT_DB_PATH.as_posix()}"
DEFAULT_AUTH_DB_PATH = APP_ROOT / ".chainlit" / "auth.db"
DEFAULT_AUTH_DB_URL = f"sqlite:///{DEFAULT_AUTH_DB_PATH.as_posix()}"


def _is_router_only_import() -> bool:
    """Return whether this import is for CLI router helpers only."""

    return os.getenv("FERMILINK_ROUTER_ONLY_IMPORT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _sync_chainlit_markdown() -> None:
    """Force Chainlit markdown to the packaged FermiLink landing content."""

    if not PACKAGED_CHAINLIT_MARKDOWN.is_file():
        return
    try:
        APP_ROOT.mkdir(parents=True, exist_ok=True)
        TARGET_CHAINLIT_MARKDOWN.write_bytes(PACKAGED_CHAINLIT_MARKDOWN.read_bytes())
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "Failed to sync %s from %s: %s",
            TARGET_CHAINLIT_MARKDOWN,
            PACKAGED_CHAINLIT_MARKDOWN,
            exc,
        )


if not _is_router_only_import():
    _sync_chainlit_markdown()

_configured_database_url = os.getenv("FERMILINK_DATABASE_URL", DEFAULT_DB_URL)
if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = _configured_database_url

_GENERATED_AUTH_SECRET = False
_configured_chainlit_auth_secret = os.getenv(
    "FERMILINK_CHAINLIT_AUTH_SECRET", ""
).strip()
if "CHAINLIT_AUTH_SECRET" not in os.environ:
    if _configured_chainlit_auth_secret:
        os.environ["CHAINLIT_AUTH_SECRET"] = _configured_chainlit_auth_secret
    else:
        os.environ["CHAINLIT_AUTH_SECRET"] = secrets.token_urlsafe(32)
        _GENERATED_AUTH_SECRET = True

import chainlit as cl
import httpx
from chainlit.config import config, public_dir
from chainlit.context import context as chainlit_context
from chainlit.data.storage_clients.base import BaseStorageClient
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.server import app as chainlit_fastapi_app
from fastapi import Body, HTTPException

RUNNER_URL = os.getenv("FERMILINK_RUNNER_URL", "http://runner:8000")
RUNNER_METRICS_TOKEN = os.getenv("FERMILINK_RUNNER_METRICS_TOKEN", "").strip()
DB_URL = os.environ.get("DATABASE_URL", _configured_database_url)
AUTH_DB_URL = os.getenv("FERMILINK_AUTH_DB_URL", DEFAULT_AUTH_DB_URL)
LOGGER = logging.getLogger(__name__)
AUTH_AUTO_REGISTER = os.getenv(
    "FERMILINK_AUTH_AUTO_REGISTER", "false"
).strip().lower() in {
    "1",
    "true",
    "yes",
}
AUTH_SIGNUP_ENABLED = os.getenv(
    "FERMILINK_AUTH_SIGNUP_ENABLED", "true"
).strip().lower() in {
    "1",
    "true",
    "yes",
}
try:
    AUTH_MIN_PASSWORD_LEN = int(os.getenv("FERMILINK_AUTH_MIN_PASSWORD_LEN", "8"))
except ValueError:
    AUTH_MIN_PASSWORD_LEN = 8
    LOGGER.warning("Invalid AUTH_MIN_PASSWORD_LEN value. Falling back to 8.")
try:
    AUTH_MAX_USERS = int(os.getenv("FERMILINK_AUTH_MAX_USERS", "0"))
except ValueError:
    AUTH_MAX_USERS = 0
    LOGGER.warning("Invalid AUTH_MAX_USERS value. Falling back to 0 (unlimited).")
if AUTH_MAX_USERS < 0:
    LOGGER.warning("AUTH_MAX_USERS=%s is invalid. Using 0 (unlimited).", AUTH_MAX_USERS)
    AUTH_MAX_USERS = 0
if _GENERATED_AUTH_SECRET:
    LOGGER.warning(
        # "CHAINLIT_AUTH_SECRET was not set. Generated a temporary secret; "
        # "users will be logged out after restart."
        "[loading]..."
    )


def _is_empty_text(value: Any) -> bool:
    """Return whether a UI text field is unset/blank."""

    return not isinstance(value, str) or not value.strip()


def _ensure_ui_branding_defaults() -> None:
    """Apply bundled UI branding when config.toml lacks explicit values.

    This keeps user-defined settings intact while restoring FermiLink branding
    for auto-generated/default Chainlit configs.
    """

    ui = config.ui

    if _is_empty_text(ui.custom_css):
        ui.custom_css = "/public/custom.css"
    if _is_empty_text(ui.custom_js):
        ui.custom_js = "/public/custom.js"
    if _is_empty_text(ui.logo_file_url):
        ui.logo_file_url = "/public/fermilink_wordmark.svg"
    if _is_empty_text(ui.default_avatar_file_url):
        ui.default_avatar_file_url = "/public/fermilink_mini_bright.svg"
    if _is_empty_text(ui.login_page_image):
        ui.login_page_image = "/public/fermilink_mini_dark.svg"
    if _is_empty_text(ui.login_page_image_dark_filter):
        ui.login_page_image_dark_filter = ""
    if _is_empty_text(ui.name) or str(ui.name).strip().lower() == "assistant":
        ui.name = "FermiLink"


_ensure_ui_branding_defaults()

DEFAULT_ARTIFACT_PREFIXES = ("outputs", "projects")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
DEFAULT_MAX_PROMPT_CHARS = 10_000
EXCLUDED_SNAPSHOT_DIRS = {
    ".git",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".chainlit",
    ".files",
    "__pycache__",
    "node_modules",
}
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _get_int_env(name: str, default: int) -> int:
    """Read an integer environment variable with fallback and logging.

    Parameters
    ----------
    name : str
        Environment variable name.
    default : int
        Fallback value when unset or invalid.

    Returns
    -------
    int
        Parsed integer value or `default`.
    """

    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        LOGGER.warning("Invalid %s=%r, using %s", name, raw, default)
        return default


def _get_bool_env(name: str, default: bool) -> bool:
    """Read a boolean environment variable with fallback and logging.

    Parameters
    ----------
    name : str
        Environment variable name.
    default : bool
        Fallback value when unset or invalid.

    Returns
    -------
    bool
        Parsed boolean value or `default`.
    """

    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    LOGGER.warning("Invalid %s=%r, using %s", name, raw, default)
    return default


def _get_float_env(name: str, default: float) -> float:
    """Read a float environment variable with fallback and logging.

    Parameters
    ----------
    name : str
        Environment variable name.
    default : float
        Fallback value when unset or invalid.

    Returns
    -------
    float
        Parsed float value or `default`.
    """

    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        LOGGER.warning("Invalid %s=%r, using %s", name, raw, default)
        return default


DEFAULT_PROMPT_GROUP = (
    os.getenv("FERMILINK_PROMPT_DEFAULT_GROUP", "average").strip().lower() or "average"
)
PROMPT_LIMITS = {
    "average": _get_int_env("FERMILINK_PROMPT_LIMIT_AVERAGE", 100),
    "star": _get_int_env("FERMILINK_PROMPT_LIMIT_STAR", 100),
}
PROMPT_DAY_TZ = os.getenv("FERMILINK_PROMPT_DAY_TZ", "UTC").strip() or "UTC"


ARTIFACT_PREFIXES = tuple(
    prefix.strip()
    for prefix in os.getenv(
        "FERMILINK_CHAINLIT_ARTIFACT_PREFIXES",
        ",".join(DEFAULT_ARTIFACT_PREFIXES),
    ).split(",")
    if prefix.strip()
)
MAX_ATTACHMENT_BYTES = _get_int_env(
    "FERMILINK_CHAINLIT_MAX_ATTACHMENT_BYTES", 50 * 1024 * 1024
)
ZIP_MIN_COUNT = _get_int_env("FERMILINK_CHAINLIT_ZIP_MIN_COUNT", 3)
MAX_PROMPT_CHARS = _get_int_env(
    "FERMILINK_RUNNER_MAX_PROMPT_CHARS", DEFAULT_MAX_PROMPT_CHARS
)
HISTORY_MAX_MESSAGES = _get_int_env("FERMILINK_CHAINLIT_HISTORY_MAX_MESSAGES", 40)
HISTORY_MAX_CHARS = _get_int_env("FERMILINK_CHAINLIT_HISTORY_MAX_CHARS", 40_000)
HISTORY_ENTRY_MAX_CHARS = _get_int_env("FERMILINK_CHAINLIT_HISTORY_ENTRY_CHARS", 4_000)
TRANSPARENCY_ENABLED = os.getenv(
    "FERMILINK_CHAINLIT_TRANSPARENCY_ENABLED", "false"
).strip().lower() in {"1", "true", "yes"}
TRANSPARENCY_MAX_ITEMS = _get_int_env("FERMILINK_CHAINLIT_TRANSPARENCY_MAX_ITEMS", 200)
TRANSPARENCY_MAX_LOG_ENTRIES = _get_int_env(
    "FERMILINK_CHAINLIT_TRANSPARENCY_MAX_LOG_ENTRIES", 50
)
TRANSPARENCY_MAX_ENTRY_CHARS = _get_int_env(
    "FERMILINK_CHAINLIT_TRANSPARENCY_MAX_ENTRY_CHARS", 500
)
FORWARD_RUNNER_LOGS = _get_bool_env("FERMILINK_CHAINLIT_FORWARD_RUNNER_LOGS", False)
PACKAGE_ROUTER_ENABLED = _get_bool_env(
    "FERMILINK_CHAINLIT_PACKAGE_ROUTER_ENABLED", True
)
PACKAGE_ROUTER_AUTO_DEFAULT = _get_bool_env(
    "FERMILINK_CHAINLIT_PACKAGE_ROUTER_AUTO", True
)
PACKAGE_ROUTER_STICKY = _get_bool_env("FERMILINK_CHAINLIT_PACKAGE_ROUTER_STICKY", True)
PACKAGE_ROUTER_MIN_SCORE = _get_int_env(
    "FERMILINK_CHAINLIT_PACKAGE_ROUTER_MIN_SCORE", 2
)
PACKAGE_ROUTER_MIN_MARGIN = _get_int_env(
    "FERMILINK_CHAINLIT_PACKAGE_ROUTER_MIN_MARGIN", 1
)
PACKAGE_ROUTER_SWITCH_MARGIN = _get_int_env(
    "FERMILINK_CHAINLIT_PACKAGE_ROUTER_SWITCH_MARGIN", 2
)
PACKAGE_SECOND_GUESS_ENABLED = _get_bool_env(
    "FERMILINK_PACKAGE_SECOND_GUESS_ENABLED", True
)
PACKAGE_SECOND_GUESS_MIN_CONFIDENCE = _get_float_env(
    "FERMILINK_CHAINLIT_PACKAGE_SECOND_GUESS_MIN_CONFIDENCE", 0.75
)
PACKAGE_SECOND_GUESS_TIMEOUT_SECONDS = _get_float_env(
    "FERMILINK_CHAINLIT_PACKAGE_SECOND_GUESS_TIMEOUT_SECONDS", 25.0
)
STREAM_PARTIAL_PERSIST_SECONDS = max(
    0.0, _get_float_env("FERMILINK_CHAINLIT_STREAM_PARTIAL_PERSIST_SECONDS", 1.0)
)
ADMISSION_POLL_INTERVAL_SECONDS = max(
    0.1, _get_float_env("FERMILINK_CHAINLIT_ADMISSION_POLL_INTERVAL_SECONDS", 0.5)
)
ADMISSION_POLL_TIMEOUT_SECONDS = max(
    0.0, _get_float_env("FERMILINK_CHAINLIT_ADMISSION_POLL_TIMEOUT_SECONDS", 0.0)
)
PACKAGE_ROUTER_RULES_FILENAME = (
    os.getenv("FERMILINK_CHAINLIT_PACKAGE_ROUTER_RULES", "router_rules.json").strip()
    or "router_rules.json"
)
SESSION_PACKAGE_ID_KEY = "selected_package_id"
SESSION_PACKAGE_SOURCE_KEY = "selected_package_source"
SESSION_PACKAGE_AUTO_KEY = "package_auto_route"
SESSION_NO_PACKAGES_NOTICE_KEY = "no_packages_notice_sent"
PACKAGE_SOURCE_MANUAL = "manual"
PACKAGE_SOURCE_AUTO = "auto"
PACKAGE_SOURCE_DEFAULT = "default"
PACKAGE_SOURCE_SECOND_GUESS = "second_guess"
PACKAGE_SOURCE_NONE = "none"
PACKAGE_COMMAND_PREFIX = "/package"

SUPPRESSED_RUNNER_LOG_MARKERS = (
    "codex_core::rollout::list: state db missing rollout path for thread",
)


@dataclass(slots=True)
class _ActiveRunBinding:
    """Track one in-flight run that may need websocket rebinding."""

    owner_keys: set[str]
    stream_session: Any | None
    run_task: asyncio.Task[Any] | None


_ACTIVE_THREADS_LOCK = asyncio.Lock()
_ACTIVE_THREADS_BY_OWNER: dict[str, set[str]] = {}
_ACTIVE_RUNS_BY_THREAD: dict[str, _ActiveRunBinding] = {}


def _normalize_package_id_safe(value: str | None) -> str | None:
    return package_router_helpers._normalize_package_id_safe(
        value,
        normalize_package_id=normalize_package_id,
    )


def _dedupe_terms(values: list[str]) -> list[str]:
    return package_router_helpers._dedupe_terms(values)


def _normalize_rule_terms(raw: Any) -> list[str]:
    return package_router_helpers._normalize_rule_terms(raw)


def _resolve_package_registry() -> tuple[list[str], str | None, Path]:
    return package_session_helpers._resolve_package_registry(
        app_root=APP_ROOT,
        resolve_scipkg_root=resolve_scipkg_root,
        load_registry=load_registry,
        normalize_package_id_safe=_normalize_package_id_safe,
        logger=LOGGER,
    )


PACKAGE_FAMILY_RULES: dict[str, dict[str, list[str]]] = (
    package_router_helpers.PACKAGE_FAMILY_RULES
)


def _load_router_config(scipkg_root: Path) -> dict[str, Any]:
    return package_router_helpers._load_router_config(
        scipkg_root,
        package_router_min_score=PACKAGE_ROUTER_MIN_SCORE,
        package_router_min_margin=PACKAGE_ROUTER_MIN_MARGIN,
        package_router_rules_filename=PACKAGE_ROUTER_RULES_FILENAME,
        normalize_package_id_safe=_normalize_package_id_safe,
        logger=LOGGER,
    )


def _package_id_terms(package_id: str) -> list[str]:
    return package_router_helpers._package_id_terms(package_id)


def _build_package_rule(
    package_id: str, config_packages: dict[str, Any]
) -> dict[str, list[str]]:
    return package_router_helpers._build_package_rule(
        package_id,
        config_packages,
        package_family_rules=PACKAGE_FAMILY_RULES,
    )


def _match_term_count(text: str, terms: list[str]) -> int:
    return package_router_helpers._match_term_count(text, terms)


def _route_package_candidate(
    user_text: str,
    package_ids: list[str],
    current_package_id: str | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    return package_router_helpers._route_package_candidate(
        user_text,
        package_ids,
        current_package_id,
        config,
        package_router_min_score=PACKAGE_ROUTER_MIN_SCORE,
        package_router_min_margin=PACKAGE_ROUTER_MIN_MARGIN,
        package_family_rules=PACKAGE_FAMILY_RULES,
    )


def _resolve_default_package_id(
    package_ids: list[str],
    active_package_id: str | None,
    config: dict[str, Any],
) -> str | None:
    return package_router_helpers._resolve_default_package_id(
        package_ids,
        active_package_id,
        config,
        normalize_package_id_safe=_normalize_package_id_safe,
    )


def _build_package_catalog(
    package_ids: list[str], active_package_id: str | None, scipkg_root: Path
) -> list[dict[str, Any]]:
    return package_router_helpers._build_package_catalog(
        package_ids,
        active_package_id,
        scipkg_root,
        load_registry=load_registry,
        logger=LOGGER,
    )


def _build_second_guess_prompt(
    *,
    user_text: str,
    current_package_id: str | None,
    package_catalog: list[dict[str, Any]],
) -> str:
    return package_router_helpers._build_second_guess_prompt(
        user_text=user_text,
        current_package_id=current_package_id,
        package_catalog=package_catalog,
    )


def _extract_first_json_object(text: str) -> dict[str, Any] | None:
    return package_router_helpers._extract_first_json_object(text)


def _coerce_confidence(value: Any) -> float:
    return package_router_helpers._coerce_confidence(value)


async def _run_package_second_guess(
    *,
    user_text: str,
    session_id: str | None,
    user_id: str | None,
    selected_package_id: str | None,
    selected_source: str,
) -> dict[str, Any]:
    return await package_session_helpers._run_package_second_guess(
        user_text=user_text,
        session_id=session_id,
        user_id=user_id,
        selected_package_id=selected_package_id,
        selected_source=selected_source,
        package_second_guess_enabled=PACKAGE_SECOND_GUESS_ENABLED,
        package_source_manual=PACKAGE_SOURCE_MANUAL,
        package_source_second_guess=PACKAGE_SOURCE_SECOND_GUESS,
        package_second_guess_timeout_seconds=PACKAGE_SECOND_GUESS_TIMEOUT_SECONDS,
        package_second_guess_min_confidence=PACKAGE_SECOND_GUESS_MIN_CONFIDENCE,
        resolve_package_registry=_resolve_package_registry,
        load_router_config=_load_router_config,
        resolve_default_package_id=_resolve_default_package_id,
        build_package_catalog=_build_package_catalog,
        build_second_guess_prompt=_build_second_guess_prompt,
        resolve_agent_runtime_policy=resolve_agent_runtime_policy,
        stream_runner=_stream_runner,
        extract_text=_extract_text,
        extract_first_json_object=_extract_first_json_object,
        normalize_package_id_safe=_normalize_package_id_safe,
        coerce_confidence=_coerce_confidence,
        logger=LOGGER,
    )


def _resolve_package_alias(raw_target: str, package_ids: list[str]) -> str | None:
    return package_router_helpers._resolve_package_alias(
        raw_target,
        package_ids,
        normalize_package_id_safe=_normalize_package_id_safe,
    )


def _get_session_auto_route_flag() -> bool:
    return package_session_helpers._get_session_auto_route_flag(
        user_session=cl.user_session,
        session_package_auto_key=SESSION_PACKAGE_AUTO_KEY,
        package_router_auto_default=PACKAGE_ROUTER_AUTO_DEFAULT,
    )


def _resolve_package_for_turn(user_text: str) -> dict[str, Any]:
    return package_session_helpers._resolve_package_for_turn(
        user_text,
        user_session=cl.user_session,
        resolve_package_registry=_resolve_package_registry,
        load_router_config=_load_router_config,
        normalize_package_id_safe=_normalize_package_id_safe,
        get_session_auto_route_flag=_get_session_auto_route_flag,
        route_package_candidate=_route_package_candidate,
        resolve_default_package_id=_resolve_default_package_id,
        session_package_id_key=SESSION_PACKAGE_ID_KEY,
        session_package_source_key=SESSION_PACKAGE_SOURCE_KEY,
        package_source_manual=PACKAGE_SOURCE_MANUAL,
        package_source_auto=PACKAGE_SOURCE_AUTO,
        package_source_default=PACKAGE_SOURCE_DEFAULT,
        package_source_none=PACKAGE_SOURCE_NONE,
        package_router_enabled=PACKAGE_ROUTER_ENABLED,
        package_router_sticky=PACKAGE_ROUTER_STICKY,
        package_router_switch_margin=PACKAGE_ROUTER_SWITCH_MARGIN,
    )


def _parse_package_command(content: str) -> tuple[str, list[str]] | None:
    return package_router_helpers._parse_package_command(
        content,
        package_command_prefix=PACKAGE_COMMAND_PREFIX,
    )


def _format_package_list(
    package_ids: list[str],
    active_package_id: str | None,
    current_package_id: str | None,
) -> str:
    return package_router_helpers._format_package_list(
        package_ids,
        active_package_id,
        current_package_id,
    )


async def _handle_package_command(message: cl.Message) -> bool:
    return await package_session_helpers._handle_package_command(
        message,
        cl_module=cl,
        parse_package_command=_parse_package_command,
        resolve_package_registry=_resolve_package_registry,
        normalize_package_id_safe=_normalize_package_id_safe,
        get_session_auto_route_flag=_get_session_auto_route_flag,
        format_package_list=_format_package_list,
        resolve_package_alias=_resolve_package_alias,
        session_package_id_key=SESSION_PACKAGE_ID_KEY,
        session_package_source_key=SESSION_PACKAGE_SOURCE_KEY,
        session_package_auto_key=SESSION_PACKAGE_AUTO_KEY,
        package_source_manual=PACKAGE_SOURCE_MANUAL,
        package_source_none=PACKAGE_SOURCE_NONE,
    )


def _normalize_status_label(value: str | None) -> str | None:
    return status_helpers._normalize_status_label(value)


async def _maybe_update_status(
    status_msg: cl.Message | None, status_label: str | None, last_status: str | None
) -> tuple[cl.Message | None, str | None]:
    return await status_helpers._maybe_update_status(
        status_msg,
        status_label,
        last_status,
        normalize_status_label=_normalize_status_label,
        cl_module=cl,
    )


def _normalize_subdir(value: str) -> str:
    return storage_helpers._normalize_subdir(value)


def _join_url(root_path: str, path: str) -> str:
    return storage_helpers._join_url(root_path, path)


LocalPublicStorageClient = storage_helpers.LocalPublicStorageClient


def _resolve_public_root() -> Path:
    return storage_helpers._resolve_public_root(
        configured_public_dir=str(public_dir),
        app_root=APP_ROOT,
        package_public_root=PACKAGE_PUBLIC_ROOT,
        is_router_only_import=_is_router_only_import(),
    )


def _build_storage_provider() -> BaseStorageClient:
    subdir = os.getenv("FERMILINK_CHAINLIT_LOCAL_STORAGE_SUBDIR", ".chainlit/artifacts")
    return storage_helpers._build_storage_provider(
        subdir=subdir,
        public_root=_resolve_public_root(),
        root_path=config.run.root_path,
    )


STORAGE_PROVIDER = _build_storage_provider()

CHAINLIT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    identifier TEXT NOT NULL,
    metadata JSONB,
    createdAt TEXT
);

CREATE TABLE IF NOT EXISTS threads (
    id UUID PRIMARY KEY,
    createdAt TEXT,
    name TEXT,
    userId UUID,
    userIdentifier TEXT,
    tags TEXT[],
    metadata JSONB,
    FOREIGN KEY(userId) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS steps (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    threadId UUID NOT NULL,
    parentId UUID,
    streaming BOOLEAN,
    waitForAnswer BOOLEAN,
    defaultOpen BOOLEAN,
    input TEXT,
    output TEXT,
    createdAt TEXT,
    start TEXT,
    end TEXT,
    generation JSONB,
    showInput TEXT,
    language TEXT,
    indent INTEGER,
    isError BOOLEAN,
    metadata JSONB,
    tags TEXT[],
    FOREIGN KEY(threadId) REFERENCES threads(id) ON DELETE CASCADE,
    FOREIGN KEY(parentId) REFERENCES steps(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS elements (
    id UUID PRIMARY KEY,
    threadId UUID,
    type TEXT,
    url TEXT,
    chainlitKey TEXT,
    name TEXT NOT NULL,
    display TEXT,
    objectKey TEXT,
    size TEXT,
    page INTEGER,
    language TEXT,
    forId UUID,
    mime TEXT,
    props JSONB,
    createdAt TEXT,
    FOREIGN KEY(threadId) REFERENCES threads(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS feedbacks (
    id UUID PRIMARY KEY,
    forId UUID NOT NULL,
    value INTEGER NOT NULL,
    comment TEXT,
    createdAt TEXT
);
"""

AUTH_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS auth_users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_login TEXT,
    group_name TEXT
);

CREATE TABLE IF NOT EXISTS auth_usage_daily (
    username TEXT NOT NULL,
    day TEXT NOT NULL,
    count INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (username, day),
    FOREIGN KEY(username) REFERENCES auth_users(username) ON DELETE CASCADE
);
"""


def _sqlite_path_from_url(url: str) -> Path | None:
    return sqlite_helpers._sqlite_path_from_url(url)


def _ensure_sqlite_schema(db_path: Path, schema_sql: str) -> None:
    sqlite_helpers._ensure_sqlite_schema(db_path, schema_sql)


def _ensure_sqlite_columns(db_path: Path, table: str, columns: dict[str, str]) -> None:
    sqlite_helpers._ensure_sqlite_columns(db_path, table, columns)


CHAINLIT_DB_PATH = _sqlite_path_from_url(DB_URL)
if CHAINLIT_DB_PATH is None:
    LOGGER.info(
        "DATABASE_URL is not sqlite. Ensure the Chainlit schema is created manually."
    )
else:
    _ensure_sqlite_schema(CHAINLIT_DB_PATH, CHAINLIT_SCHEMA_SQL)
    _ensure_sqlite_columns(
        CHAINLIT_DB_PATH,
        "steps",
        {
            "streaming": "BOOLEAN",
            "waitForAnswer": "BOOLEAN",
            "defaultOpen": "BOOLEAN",
            "start": "TEXT",
            "end": "TEXT",
            "showInput": "TEXT",
            "generation": "TEXT",
            "metadata": "TEXT",
            "tags": "TEXT",
            "input": "TEXT",
            "output": "TEXT",
            "language": "TEXT",
            "indent": "INTEGER",
            "isError": "BOOLEAN",
            "createdAt": "TEXT",
            "parentId": "UUID",
        },
    )
    _ensure_sqlite_columns(
        CHAINLIT_DB_PATH,
        "elements",
        {
            "props": "TEXT",
            "chainlitKey": "TEXT",
            "objectKey": "TEXT",
            "display": "TEXT",
            "size": "TEXT",
            "language": "TEXT",
            "page": "INTEGER",
            "forId": "UUID",
            "mime": "TEXT",
            "createdAt": "TEXT",
        },
    )

AUTH_DB_PATH = _sqlite_path_from_url(AUTH_DB_URL)
if AUTH_DB_PATH is None:
    AUTH_DB_PATH = APP_ROOT / ".chainlit" / "auth.db"
    LOGGER.warning(
        "AUTH_DB_URL is not sqlite. Falling back to %s for auth credentials.",
        AUTH_DB_PATH,
    )
_ensure_sqlite_schema(AUTH_DB_PATH, AUTH_SCHEMA_SQL)
_ensure_sqlite_columns(
    AUTH_DB_PATH,
    "auth_users",
    {
        "group_name": "TEXT",
    },
)


def _normalize_username(username: str) -> str:
    return auth_helpers._normalize_username(username)


def _validate_password(password: str) -> bool:
    return auth_helpers._validate_password(
        password,
        auth_min_password_len=AUTH_MIN_PASSWORD_LEN,
    )


def _validate_email(email: str) -> bool:
    return auth_helpers._validate_email(
        email,
        email_pattern=EMAIL_PATTERN,
    )


def _count_auth_users() -> int:
    return auth_helpers._count_auth_users(open_auth_db=_open_auth_db)


def _signup_status_payload() -> dict[str, Any]:
    return auth_helpers._signup_status_payload(
        count_auth_users=_count_auth_users,
        auth_signup_enabled=AUTH_SIGNUP_ENABLED,
        auth_max_users=AUTH_MAX_USERS,
        auth_min_password_len=AUTH_MIN_PASSWORD_LEN,
    )


def _normalize_group(group_name: str | None) -> str:
    return auth_helpers._normalize_group(
        group_name,
        default_prompt_group=DEFAULT_PROMPT_GROUP,
    )


def _get_prompt_timezone() -> timezone:
    return auth_helpers._get_prompt_timezone(
        prompt_day_tz=PROMPT_DAY_TZ,
        logger=LOGGER,
    )


def _current_usage_day(now: datetime | None = None) -> tuple[str, datetime]:
    return auth_helpers._current_usage_day(
        now,
        get_prompt_timezone=_get_prompt_timezone,
    )


def _next_usage_reset(now: datetime | None = None) -> datetime:
    return auth_helpers._next_usage_reset(
        now,
        current_usage_day=_current_usage_day,
    )


def _get_prompt_limit(group_name: str) -> int:
    return auth_helpers._get_prompt_limit(
        group_name,
        normalize_group=_normalize_group,
        prompt_limits=PROMPT_LIMITS,
        default_prompt_group=DEFAULT_PROMPT_GROUP,
    )


def _get_user_group(username: str) -> str:
    return auth_helpers._get_user_group(
        username,
        normalize_username=_normalize_username,
        default_prompt_group=DEFAULT_PROMPT_GROUP,
        open_auth_db=_open_auth_db,
        normalize_group=_normalize_group,
    )


def _get_current_user_identifier() -> str | None:
    return activity_helpers._get_current_user_identifier(user_session=cl.user_session)


def _get_current_chainlit_session_identifier() -> str | None:
    return activity_helpers._get_current_chainlit_session_identifier(
        chainlit_context=chainlit_context
    )


def _get_current_chainlit_session_object() -> Any | None:
    return activity_helpers._get_current_chainlit_session_object(
        chainlit_context=chainlit_context
    )


def _resolve_activity_owner_keys(user_identifier: str | None = None) -> list[str]:
    return activity_helpers._resolve_activity_owner_keys(
        user_identifier,
        get_current_user_identifier=_get_current_user_identifier,
        get_current_chainlit_session_object=_get_current_chainlit_session_object,
        get_current_chainlit_session_identifier=_get_current_chainlit_session_identifier,
    )


def _owner_scope_matches(
    binding_owner_keys: set[str], candidate_owner_keys: set[str]
) -> bool:
    return activity_helpers._owner_scope_matches(
        binding_owner_keys,
        candidate_owner_keys,
    )


async def _thread_has_active_run_for_owner(
    thread_id: str | None, owner_keys: list[str]
) -> bool:
    return await activity_helpers._thread_has_active_run_for_owner(
        thread_id,
        owner_keys,
        active_threads_lock=_ACTIVE_THREADS_LOCK,
        active_runs_by_thread=_ACTIVE_RUNS_BY_THREAD,
        owner_scope_matches=_owner_scope_matches,
    )


async def _cancel_active_run_for_thread(
    thread_id: str | None, owner_keys: list[str]
) -> bool:
    return await activity_helpers._cancel_active_run_for_thread(
        thread_id,
        owner_keys,
        active_threads_lock=_ACTIVE_THREADS_LOCK,
        active_runs_by_thread=_ACTIVE_RUNS_BY_THREAD,
        owner_scope_matches=_owner_scope_matches,
    )


async def _mark_thread_running(owner_keys: list[str], thread_id: str | None) -> None:
    await activity_helpers._mark_thread_running(
        owner_keys,
        thread_id,
        active_threads_lock=_ACTIVE_THREADS_LOCK,
        active_threads_by_owner=_ACTIVE_THREADS_BY_OWNER,
    )


async def _mark_thread_stopped(owner_keys: list[str], thread_id: str | None) -> None:
    await activity_helpers._mark_thread_stopped(
        owner_keys,
        thread_id,
        active_threads_lock=_ACTIVE_THREADS_LOCK,
        active_threads_by_owner=_ACTIVE_THREADS_BY_OWNER,
    )


async def _get_active_threads_for_owner_keys(owner_keys: list[str]) -> list[str]:
    return await activity_helpers._get_active_threads_for_owner_keys(
        owner_keys,
        active_threads_lock=_ACTIVE_THREADS_LOCK,
        active_threads_by_owner=_ACTIVE_THREADS_BY_OWNER,
    )


async def _register_active_run(
    thread_id: str | None,
    owner_keys: list[str],
    stream_session: Any | None,
    run_task: asyncio.Task[Any] | None,
) -> None:
    await activity_helpers._register_active_run(
        thread_id,
        owner_keys,
        stream_session,
        run_task,
        active_threads_lock=_ACTIVE_THREADS_LOCK,
        active_runs_by_thread=_ACTIVE_RUNS_BY_THREAD,
        active_run_binding_cls=_ActiveRunBinding,
    )


async def _unregister_active_run(thread_id: str | None) -> None:
    await activity_helpers._unregister_active_run(
        thread_id,
        active_threads_lock=_ACTIVE_THREADS_LOCK,
        active_runs_by_thread=_ACTIVE_RUNS_BY_THREAD,
    )


async def _rebind_active_run_session(
    thread_id: str | None, owner_keys: list[str]
) -> None:
    await activity_helpers._rebind_active_run_session(
        thread_id,
        owner_keys,
        active_threads_lock=_ACTIVE_THREADS_LOCK,
        active_runs_by_thread=_ACTIVE_RUNS_BY_THREAD,
        owner_scope_matches=_owner_scope_matches,
        get_current_chainlit_session_object=_get_current_chainlit_session_object,
        logger=LOGGER,
    )


async def _sync_running_threads_window_state() -> None:
    await activity_helpers._sync_running_threads_window_state(
        resolve_activity_owner_keys=_resolve_activity_owner_keys,
        get_active_threads_for_owner_keys=_get_active_threads_for_owner_keys,
        get_current_thread_id=_get_current_thread_id,
        thread_has_active_run_for_owner=_thread_has_active_run_for_owner,
        get_current_chainlit_session_object=_get_current_chainlit_session_object,
        rebind_active_run_session=_rebind_active_run_session,
        send_window_message=cl.send_window_message,
        logger=LOGGER,
    )


def _consume_prompt_quota(username: str) -> tuple[bool, int, int, str, datetime]:
    return auth_helpers._consume_prompt_quota(
        username,
        normalize_username=_normalize_username,
        get_user_group=_get_user_group,
        get_prompt_limit=_get_prompt_limit,
        default_prompt_group=DEFAULT_PROMPT_GROUP,
        next_usage_reset=_next_usage_reset,
        current_usage_day=_current_usage_day,
        open_auth_db=_open_auth_db,
    )


def _open_auth_db() -> sqlite3.Connection:
    return auth_helpers._open_auth_db(auth_db_path=AUTH_DB_PATH)


def _get_user(username: str) -> sqlite3.Row | None:
    return auth_helpers._get_user(
        username,
        normalize_username=_normalize_username,
        open_auth_db=_open_auth_db,
    )


def _create_user(username: str, password: str) -> sqlite3.Row | None:
    return auth_helpers._create_user(
        username,
        password,
        normalize_username=_normalize_username,
        validate_password=_validate_password,
        open_auth_db=_open_auth_db,
        default_prompt_group=DEFAULT_PROMPT_GROUP,
        get_user=_get_user,
        pbkdf2_sha256=pbkdf2_sha256,
    )


def _register_signup_user(
    username: str, password: str
) -> tuple[sqlite3.Row | None, str]:
    return auth_helpers._register_signup_user(
        username,
        password,
        normalize_username=_normalize_username,
        validate_email=_validate_email,
        validate_password=_validate_password,
        open_auth_db=_open_auth_db,
        auth_signup_enabled=AUTH_SIGNUP_ENABLED,
        auth_max_users=AUTH_MAX_USERS,
        default_prompt_group=DEFAULT_PROMPT_GROUP,
        get_user=_get_user,
        pbkdf2_sha256=pbkdf2_sha256,
        logger=LOGGER,
    )


def _update_last_login(username: str) -> None:
    auth_helpers._update_last_login(username, open_auth_db=_open_auth_db)


def _authenticate_user(username: str, password: str) -> str | None:
    return auth_helpers._authenticate_user(
        username,
        password,
        normalize_username=_normalize_username,
        get_user=_get_user,
        auth_auto_register=AUTH_AUTO_REGISTER,
        create_user=_create_user,
        update_last_login=_update_last_login,
        pbkdf2_sha256=pbkdf2_sha256,
    )


@cl.data_layer
def get_data_layer():
    """Build the Chainlit SQLAlchemy data layer instance.

    Returns
    -------
    SQLAlchemyDataLayer
        Data layer bound to configured database and storage provider.
    """

    return SQLAlchemyDataLayer(conninfo=DB_URL, storage_provider=STORAGE_PROVIDER)


@cl.password_auth_callback
def auth_callback(username: str, password: str):
    """Authenticate Chainlit password login requests.

    Parameters
    ----------
    username : str
        Login username.
    password : str
        Login password.

    Returns
    -------
    cl.User or None
        Chainlit user object on success, otherwise `None`.
    """

    user_identifier = _authenticate_user(username, password)
    if not user_identifier:
        return None
    return cl.User(
        identifier=user_identifier,
        metadata={"provider": "password"},
    )


@chainlit_fastapi_app.post("/api/auth/signup/status")
async def signup_status():
    """Expose current self-signup availability for login page UI.

    Returns
    -------
    dict[str, Any]
        Signup status payload with enabled flag and limits.
    """

    return _signup_status_payload()


@chainlit_fastapi_app.post("/api/auth/signup")
async def signup_api(payload: dict[str, Any] = Body(...)):
    """Register a new user account via public signup form.

    Parameters
    ----------
    payload : dict[str, Any]
        JSON payload with `email`, `password`, `confirm_password`.

    Returns
    -------
    dict[str, Any]
        Success payload when account is created.
    """

    email = payload.get("email")
    password = payload.get("password")
    confirm_password = payload.get("confirm_password")

    if not isinstance(email, str) or not isinstance(password, str):
        raise HTTPException(
            status_code=400, detail="Email and password are required fields."
        )
    if not isinstance(confirm_password, str):
        raise HTTPException(
            status_code=400,
            detail="Please provide password confirmation.",
        )

    normalized_email = _normalize_username(email)
    if not normalized_email or not _validate_email(normalized_email):
        raise HTTPException(
            status_code=400,
            detail="Please provide a valid email address.",
        )

    if password != confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match.")
    if not _validate_password(password):
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {AUTH_MIN_PASSWORD_LEN} characters long.",
        )

    created, reason = _register_signup_user(normalized_email, password)
    if created is None:
        if reason == "disabled_by_admin":
            raise HTTPException(status_code=403, detail="Sign up is disabled by admin.")
        if reason == "user_limit_reached":
            raise HTTPException(
                status_code=403,
                detail=f"Sign up is closed. User limit reached ({AUTH_MAX_USERS}).",
            )
        if reason == "already_exists":
            raise HTTPException(
                status_code=409,
                detail="This email is already registered.",
            )
        if reason == "invalid_username":
            raise HTTPException(
                status_code=400,
                detail="Please provide a valid email address.",
            )
        if reason == "invalid_password":
            raise HTTPException(
                status_code=400,
                detail=f"Password must be at least {AUTH_MIN_PASSWORD_LEN} characters long.",
            )
        raise HTTPException(
            status_code=500,
            detail="Unable to create account right now. Please try again later.",
        )

    return {
        "ok": True,
        "identifier": normalized_email,
        "message": "Account created. Please sign in.",
    }


_CHAINLIT_ROOT_PATH = (config.run.root_path or "").strip()
if _CHAINLIT_ROOT_PATH and _CHAINLIT_ROOT_PATH != "/":
    _ROOT_PREFIX = _CHAINLIT_ROOT_PATH.rstrip("/")
    chainlit_fastapi_app.add_api_route(
        f"{_ROOT_PREFIX}/api/auth/signup/status",
        signup_status,
        methods=["POST"],
        include_in_schema=False,
    )
    chainlit_fastapi_app.add_api_route(
        f"{_ROOT_PREFIX}/api/auth/signup",
        signup_api,
        methods=["POST"],
        include_in_schema=False,
    )


@cl.set_starters
async def set_starters():
    """Provide predefined starter prompts for the chat UI.

    Returns
    -------
    list of cl.Starter
        Starter prompt cards rendered by Chainlit.
    """

    return [
        cl.Starter(
            label="electronic structure calculations",
            message=(
                "calculate the ground-state energy of a water molecule using the psi4 python interface using hf/3-21g and plot the convergence of the self-consistent field iterations"
            ),
        ),
        cl.Starter(
            label="molecular dynamics simulations",
            message="run energy minimization for a tip3p water molecule and plot the minimized molecular structure",
        ),
        cl.Starter(
            label="light-matter dynamics",
            message=(
                "run a weakly excited two-level system coupled to the 2D photonic crystal and plot the excited-state population dynamics and final electric field distribution"
            ),
        ),
    ]


@cl.on_chat_start
async def on_chat_start() -> None:
    """Restore in-progress indicators for active runs after reconnect."""

    await _sync_running_threads_window_state()


@cl.on_chat_resume
async def on_chat_resume(thread: dict):
    """Restore session metadata when a previous chat thread is resumed.

    Parameters
    ----------
    thread : dict
        Thread payload from Chainlit persistence.

    Returns
    -------
    None
        Session state is mutated in place.
    """

    metadata = thread.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}

    session_id = metadata.get("session_id")
    if isinstance(session_id, str) and session_id:
        cl.user_session.set("session_id", session_id)

    history = metadata.get("chat_history")
    if isinstance(history, list):
        restored: list[tuple[str, str]] = []
        for item in history:
            if (
                isinstance(item, (list, tuple))
                and len(item) == 2
                and isinstance(item[0], str)
                and isinstance(item[1], str)
            ):
                restored.append((item[0], item[1]))
        if restored:
            cl.user_session.set("chat_history", restored)

    selected_package = metadata.get(SESSION_PACKAGE_ID_KEY)
    normalized_package = _normalize_package_id_safe(selected_package)
    if normalized_package:
        cl.user_session.set(SESSION_PACKAGE_ID_KEY, normalized_package)

    selected_source = metadata.get(SESSION_PACKAGE_SOURCE_KEY)
    if isinstance(selected_source, str) and selected_source:
        cl.user_session.set(SESSION_PACKAGE_SOURCE_KEY, selected_source)

    auto_route = metadata.get(SESSION_PACKAGE_AUTO_KEY)
    if isinstance(auto_route, bool):
        cl.user_session.set(SESSION_PACKAGE_AUTO_KEY, auto_route)

    await _sync_running_threads_window_state()


@cl.on_window_message
async def on_window_message(payload: Any) -> None:
    """Handle frontend control messages for reconnect-safe run management."""

    if not isinstance(payload, dict):
        return

    message_type = str(payload.get("type") or "").strip().lower()
    if message_type not in {"probe_active_run", "stop_active_run"}:
        return

    thread_id = payload.get("thread_id")
    if isinstance(thread_id, str):
        thread_id = thread_id.strip()
    else:
        thread_id = None
    if not thread_id:
        thread_id = _get_current_thread_id()
    if not thread_id:
        return

    owner_keys = _resolve_activity_owner_keys()
    if message_type == "probe_active_run":
        if not await _thread_has_active_run_for_owner(thread_id, owner_keys):
            return
        await _rebind_active_run_session(thread_id, owner_keys)
        current_session = _get_current_chainlit_session_object()
        if current_session is not None:
            try:
                await current_session.emit("task_start", {})
                await current_session.emit(
                    "first_interaction",
                    {"interaction": "resume", "thread_id": thread_id},
                )
            except Exception as exc:
                LOGGER.debug("Failed to probe-sync active run state: %s", exc)
        await cl.send_window_message(
            {
                "type": "assistant_thinking",
                "status": "start",
                "thread_id": thread_id,
            }
        )
        return

    cancelled = await _cancel_active_run_for_thread(thread_id, owner_keys)
    if cancelled:
        current_session = _get_current_chainlit_session_object()
        if current_session is not None:
            try:
                await current_session.emit("task_end", {})
            except Exception as exc:
                LOGGER.debug("Failed to emit task_end after manual stop: %s", exc)
        await cl.send_window_message(
            {
                "type": "assistant_thinking",
                "status": "end",
                "thread_id": thread_id,
            }
        )
        LOGGER.info("Cancelled active run via window control for thread %s", thread_id)


def _extract_text(payload: dict) -> str | None:
    return chat_helpers._extract_text(payload)


def _extract_command(payload: dict) -> str | None:
    return chat_helpers._extract_command(payload)


def _truncate_history_entry(text: str) -> str:
    return chat_helpers._truncate_history_entry(
        text,
        history_entry_max_chars=HISTORY_ENTRY_MAX_CHARS,
    )


def _append_history(
    history: list[tuple[str, str]], role: str, content: str
) -> list[tuple[str, str]]:
    return chat_helpers._append_history(
        history,
        role,
        content,
        history_entry_max_chars=HISTORY_ENTRY_MAX_CHARS,
        history_max_messages=HISTORY_MAX_MESSAGES,
        history_max_chars=HISTORY_MAX_CHARS,
    )


def _format_history(history: list[tuple[str, str]]) -> str:
    return chat_helpers._format_history(history)


def _build_prompt(history: list[tuple[str, str]], user_text: str) -> str:
    return chat_helpers._build_prompt(
        history,
        user_text,
        max_prompt_chars=MAX_PROMPT_CHARS,
    )


def _resolve_workspaces_root() -> Path:
    return artifact_helpers._resolve_workspaces_root(
        resolve_default_workspaces_root=resolve_default_workspaces_root,
        cwd=Path.cwd(),
    )


def _extract_candidate_paths(text: str) -> list[str]:
    return artifact_helpers._extract_candidate_paths(text)


def _resolve_artifact_path(repo_root: Path, token: str) -> tuple[Path, Path] | None:
    return artifact_helpers._resolve_artifact_path(
        repo_root,
        token,
        artifact_prefixes=ARTIFACT_PREFIXES,
    )


def _element_for_path(path: Path, relative: Path):
    return artifact_helpers._element_for_path(
        path,
        relative,
        image_exts=IMAGE_EXTS,
        cl_module=cl,
    )


def _snapshot_repo(repo_root: Path) -> dict[str, tuple[int, int]]:
    return artifact_helpers._snapshot_repo(
        repo_root,
        excluded_snapshot_dirs=EXCLUDED_SNAPSHOT_DIRS,
    )


def _diff_snapshots(
    before: dict[str, tuple[int, int]],
    after: dict[str, tuple[int, int]],
) -> tuple[list[str], list[str]]:
    return artifact_helpers._diff_snapshots(before, after)


def _truncate_items(items: list[str], max_items: int) -> list[str]:
    return artifact_helpers._truncate_items(items, max_items)


def _dedupe_preserve(items: list[str]) -> list[str]:
    return artifact_helpers._dedupe_preserve(items)


def _truncate_entry(text: str) -> str:
    return artifact_helpers._truncate_entry(
        text,
        transparency_max_entry_chars=TRANSPARENCY_MAX_ENTRY_CHARS,
    )


def _format_transparency_report(
    active_package: str | None,
    tool_calls: list[str],
    commands_run: list[str],
    created_files: list[str],
    modified_files: list[str],
    log_entries: list[str],
    error_entries: list[str],
) -> str:
    return artifact_helpers._format_transparency_report(
        active_package,
        tool_calls,
        commands_run,
        created_files,
        modified_files,
        log_entries,
        error_entries,
        transparency_max_items=TRANSPARENCY_MAX_ITEMS,
        transparency_max_log_entries=TRANSPARENCY_MAX_LOG_ENTRIES,
        transparency_max_entry_chars=TRANSPARENCY_MAX_ENTRY_CHARS,
    )


async def _attach_artifacts_from_text(
    text: str, session_id: str | None, message: cl.Message
) -> None:
    await artifact_helpers._attach_artifacts_from_text(
        text,
        session_id,
        message,
        resolve_workspaces_root=_resolve_workspaces_root,
        extract_candidate_paths=_extract_candidate_paths,
        resolve_artifact_path=_resolve_artifact_path,
        element_for_path=_element_for_path,
        max_attachment_bytes=MAX_ATTACHMENT_BYTES,
        image_exts=IMAGE_EXTS,
        zip_min_count=ZIP_MIN_COUNT,
        cl_module=cl,
        logger=LOGGER,
    )


async def _stream_runner(payload: dict):
    async for event_type, data in runner_helpers._stream_runner(
        payload,
        runner_url=RUNNER_URL,
        httpx_module=httpx,
    ):
        yield event_type, data


def _build_runner_admission_params(
    session_id: str | None, user_id: str | None
) -> dict[str, str]:
    return runner_helpers._build_runner_admission_params(session_id, user_id)


async def _probe_runner_admission(
    client: httpx.AsyncClient, *, session_id: str | None, user_id: str | None
) -> dict[str, Any] | None:
    return await runner_helpers._probe_runner_admission(
        client,
        runner_url=RUNNER_URL,
        session_id=session_id,
        user_id=user_id,
        runner_metrics_token=RUNNER_METRICS_TOKEN,
        logger=LOGGER,
    )


async def _wait_for_runner_admission_slot(
    *,
    session_id: str | None,
    user_id: str | None,
    on_queued: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    return await runner_helpers._wait_for_runner_admission_slot(
        session_id=session_id,
        user_id=user_id,
        on_queued=on_queued,
        admission_poll_timeout_seconds=ADMISSION_POLL_TIMEOUT_SECONDS,
        admission_poll_interval_seconds=ADMISSION_POLL_INTERVAL_SECONDS,
        runner_url=RUNNER_URL,
        runner_metrics_token=RUNNER_METRICS_TOKEN,
        logger=LOGGER,
        httpx_module=httpx,
    )


def _runner_http_error_detail(exc: httpx.HTTPStatusError) -> str:
    return runner_helpers._runner_http_error_detail(exc)


def _build_admission_queued_notice(snapshot: dict[str, Any]) -> str:
    return runner_helpers._build_admission_queued_notice(snapshot)


def _get_current_thread_id() -> str | None:
    return activity_helpers._get_current_thread_id(chainlit_context=chainlit_context)


def _should_surface_runner_log(text: str) -> bool:
    return runner_helpers._should_surface_runner_log(
        text,
        forward_runner_logs=FORWARD_RUNNER_LOGS,
        suppressed_runner_log_markers=SUPPRESSED_RUNNER_LOG_MARKERS,
    )


@cl.on_message
async def on_message(message: cl.Message):
    """Handle incoming user messages and stream Codex execution output.

    Parameters
    ----------
    message : cl.Message
        User message from Chainlit UI.

    Returns
    -------
    None
        Sends assistant response, optional artifacts, and transparency report.
    """

    if await _handle_package_command(message):
        return

    user_identifier = _get_current_user_identifier()
    if user_identifier:
        allowed, used, limit, group_name, reset_at = _consume_prompt_quota(
            user_identifier
        )
        if not allowed:
            reset_label = reset_at.strftime("%Y-%m-%d %H:%M %Z")
            limit_msg = (
                f"Daily prompt limit reached. "
                f"Used {used}/{limit} prompts today. "
                f"Limits reset at {reset_label}."
            )
            await cl.Message(content=limit_msg).send()
            return

    current_thread_id = _get_current_thread_id()
    current_owner_keys = _resolve_activity_owner_keys(user_identifier)
    if await _thread_has_active_run_for_owner(current_thread_id, current_owner_keys):
        await _sync_running_threads_window_state()
        await cl.Message(
            content=(
                "[runner] This chat is still processing a previous request. "
                "Please stop that run first or wait for completion."
            ),
            author="status",
            type="system_message",
        ).send()
        return

    session_id = cl.user_session.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        session_id = current_thread_id

    async def _send_queued_notice(snapshot: dict[str, Any]) -> None:
        await cl.Message(
            content=_build_admission_queued_notice(snapshot),
            author="status",
            type="system_message",
        ).send()

    admission_result = await _wait_for_runner_admission_slot(
        session_id=session_id,
        user_id=user_identifier,
        on_queued=_send_queued_notice,
    )
    if not bool(admission_result.get("ok", True)):
        await cl.Message(
            content=(
                "[runner] Busy: timed out waiting for an execution slot. "
                "Please retry shortly."
            )
        ).send()
        return

    package_decision = _resolve_package_for_turn(message.content)
    selected_package_id = package_decision.get("package_id")
    package_reason = package_decision.get("reason")
    scipkg_root_text = package_decision.get("scipkg_root")
    installed_package_count = package_decision.get("installed_package_count")
    selected_source = package_decision.get("source", PACKAGE_SOURCE_NONE)
    if not isinstance(selected_source, str) or not selected_source:
        selected_source = PACKAGE_SOURCE_NONE

    previous_package_id = cl.user_session.get(SESSION_PACKAGE_ID_KEY)
    if isinstance(previous_package_id, str):
        previous_package_id = _normalize_package_id_safe(previous_package_id)
    else:
        previous_package_id = None
    previous_source = cl.user_session.get(SESSION_PACKAGE_SOURCE_KEY)
    if not isinstance(previous_source, str) or not previous_source:
        previous_source = PACKAGE_SOURCE_NONE

    _get_session_auto_route_flag()

    if package_reason == "no_packages":
        already_notified = bool(cl.user_session.get(SESSION_NO_PACKAGES_NOTICE_KEY))
        if not already_notified:
            location = (
                str(scipkg_root_text)
                if isinstance(scipkg_root_text, str) and scipkg_root_text
                else "scientific_packages/"
            )
            count_text = (
                str(installed_package_count)
                if isinstance(installed_package_count, int)
                else "0"
            )
            await cl.Message(
                content=(
                    "[package] No installed scientific package was found for routing. "
                    f"(detected: {count_text} package(s) under `{location}`)\n"
                    "Run `fermilink install <package> --activate` first to enable "
                    "automatic package overlay into workspace `repo/`."
                ),
                author="status",
                type="system_message",
            ).send()
            cl.user_session.set(SESSION_NO_PACKAGES_NOTICE_KEY, True)
    else:
        cl.user_session.set(SESSION_NO_PACKAGES_NOTICE_KEY, False)

    router_note = ""
    second_guess_result = await _run_package_second_guess(
        user_text=message.content,
        session_id=session_id,
        user_id=user_identifier,
        selected_package_id=selected_package_id,
        selected_source=selected_source,
    )
    if isinstance(second_guess_result, dict):
        updated_session_id = second_guess_result.get("session_id")
        if isinstance(updated_session_id, str) and updated_session_id:
            session_id = updated_session_id
            cl.user_session.set("session_id", updated_session_id)

        updated_package_id = second_guess_result.get("package_id")
        if isinstance(updated_package_id, str) and updated_package_id:
            selected_package_id = updated_package_id
        elif updated_package_id is None:
            selected_package_id = None

        updated_source = second_guess_result.get("source")
        if isinstance(updated_source, str) and updated_source:
            selected_source = updated_source

        note = second_guess_result.get("note")
        if isinstance(note, str) and note:
            router_note = note

    cl.user_session.set(SESSION_PACKAGE_ID_KEY, selected_package_id)
    cl.user_session.set(SESSION_PACKAGE_SOURCE_KEY, selected_source)

    package_changed = (
        selected_package_id != previous_package_id or selected_source != previous_source
    )
    if package_changed and selected_source in {
        PACKAGE_SOURCE_AUTO,
        PACKAGE_SOURCE_DEFAULT,
        PACKAGE_SOURCE_SECOND_GUESS,
    }:
        if selected_package_id:
            status_note = ""
            if selected_source == PACKAGE_SOURCE_SECOND_GUESS and router_note:
                status_note = f", detail: {router_note}"
            await cl.Message(
                content=(
                    f"[package] Using `{selected_package_id}` "
                    f"(selection: `{selected_source}`{status_note})"
                ),
                author="status",
                type="system_message",
            ).send()
        elif previous_package_id:
            await cl.Message(
                content="[package] No scientific package selected for this turn.",
                author="status",
                type="system_message",
            ).send()

    history: list[tuple[str, str]] = cl.user_session.get("chat_history") or []
    prompt_body = _build_prompt(history, message.content)
    prompt = f"{UNIFIED_MEMORY_PROMPT_PREFIX}{prompt_body.strip()}\n"
    runtime_policy = resolve_agent_runtime_policy()
    payload = {
        "session_id": session_id,
        "user_prompt": prompt,
        "provider": runtime_policy.provider,
    }
    if runtime_policy.sandbox_policy != "bypass":
        payload["sandbox"] = runtime_policy.sandbox_mode
    if isinstance(user_identifier, str) and user_identifier:
        payload["user_id"] = user_identifier
    if isinstance(selected_package_id, str) and selected_package_id:
        payload["package_id"] = selected_package_id

    assistant_msg = cl.Message(content="")
    await assistant_msg.send()
    steps: dict[str, cl.Step] = {}
    assistant_buffer = ""
    had_output = False
    tool_calls: list[str] = []
    commands_run: list[str] = []
    log_entries: list[str] = []
    if router_note:
        log_entries.append(f"[router] {router_note}")
    error_entries: list[str] = []
    repo_root: Path | None = None
    snapshot_before: dict[str, tuple[int, int]] | None = None
    active_package_id = (
        selected_package_id if isinstance(selected_package_id, str) else None
    )
    status_msg: cl.Message | None = None
    last_status: str | None = None
    thread_id = current_thread_id
    thread_owner_keys = current_owner_keys
    stream_session = _get_current_chainlit_session_object()
    run_task = asyncio.current_task()
    if thread_id:
        await _mark_thread_running(thread_owner_keys, thread_id)
        await _register_active_run(
            thread_id, thread_owner_keys, stream_session, run_task
        )
    thinking_start_payload: dict[str, str] = {
        "type": "assistant_thinking",
        "status": "start",
    }
    if thread_id:
        thinking_start_payload["thread_id"] = thread_id
    await cl.send_window_message(thinking_start_payload)
    last_partial_persist_at = time.monotonic()

    async def _persist_partial_if_due(force: bool = False) -> None:
        """Persist partial assistant text so refresh can recover in-progress output."""

        nonlocal last_partial_persist_at
        if not had_output:
            return

        now = time.monotonic()
        if not force:
            if STREAM_PARTIAL_PERSIST_SECONDS <= 0:
                return
            if now - last_partial_persist_at < STREAM_PARTIAL_PERSIST_SECONDS:
                return

        data_layer = get_data_layer()
        if data_layer is None:
            return

        assistant_msg.content = assistant_buffer
        step_dict = assistant_msg.to_dict()
        try:
            asyncio.create_task(data_layer.update_step(step_dict))
        except Exception as exc:
            LOGGER.debug("Skipping partial assistant persistence: %s", exc)
            return
        last_partial_persist_at = now

    try:
        async for event_type, data in _stream_runner(payload):
            if event_type == "meta":
                try:
                    meta = json.loads(data)
                except json.JSONDecodeError:
                    continue
                package_meta = meta.get("package")
                if isinstance(package_meta, dict):
                    package_id = package_meta.get("package_id")
                    if isinstance(package_id, str) and package_id:
                        active_package_id = package_id
                session_id = meta.get("session_id") or session_id
                if session_id:
                    cl.user_session.set("session_id", session_id)
                    if repo_root is None:
                        repo_root = _resolve_workspaces_root() / session_id / "repo"
                    if snapshot_before is None and repo_root.exists():
                        snapshot_before = _snapshot_repo(repo_root)

            elif event_type == "codex":
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue

                item = event.get("item")
                if not isinstance(item, dict):
                    item = {}
                item_type = item.get("type") or event.get("type") or ""
                status_label = item_type
                if item_type in {
                    "command_execution",
                    "exec_command_begin",
                    "command",
                    "tool_call",
                    "exec",
                }:
                    command_text = _extract_command(item) or _extract_command(event)
                    if command_text:
                        status_label = f"command: {command_text}"
                status_msg, last_status = await _maybe_update_status(
                    status_msg, status_label, last_status
                )

                if item_type.startswith("agent_message"):
                    text = _extract_text(event) or ""
                    if text:
                        assistant_buffer += text
                        had_output = True
                        await assistant_msg.stream_token(text)
                        await _persist_partial_if_due()

                elif item_type == "error":
                    message_text = event.get("message") or _extract_text(event)
                    if message_text:
                        error_entries.append(message_text)
                        msg = f"\n\n[error] {message_text}"
                        assistant_buffer += msg
                        had_output = True
                        await assistant_msg.stream_token(msg)
                        await _persist_partial_if_due()

                elif item_type in {"turn.failed", "stream_error"}:
                    message_text = _extract_text(event)
                    if message_text:
                        error_entries.append(message_text)
                        msg = f"\n\n[error] {message_text}"
                        assistant_buffer += msg
                        had_output = True
                        await assistant_msg.stream_token(msg)
                        await _persist_partial_if_due()

                elif item_type == "exec_command_begin":
                    command_text = _extract_command(event) or _extract_command(item)
                    if command_text:
                        commands_run.append(str(command_text))
                        tool_calls.append(str(command_text))
                        step = cl.Step(
                            name="Command", type="tool", content=str(command_text)
                        )
                        await step.send()
                        process_id = event.get("process_id") or item.get("process_id")
                        if process_id is not None:
                            steps[str(process_id)] = step

                elif item_type == "exec_command_output_delta":
                    process_id = event.get("process_id") or item.get("process_id")
                    output_text = _extract_text(event)
                    if process_id is not None and output_text:
                        step = steps.get(str(process_id))
                        if step:
                            prefix = ""
                            if step.content and not step.content.endswith("\n"):
                                prefix = "\n"
                            step.content = (step.content or "") + prefix + output_text
                            await step.update()

                elif item_type == "exec_command_end":
                    process_id = event.get("process_id") or item.get("process_id")
                    if process_id is not None:
                        step = steps.pop(str(process_id), None)
                        if step:
                            await step.update()

                elif item_type in {"command", "tool_call", "exec"}:
                    command_text = _extract_command(item) or _extract_command(event)
                    if command_text:
                        tool_calls.append(str(command_text))
                        step = cl.Step(
                            name="Command", type="tool", content=str(command_text)
                        )
                        await step.send()

            elif event_type == "log":
                try:
                    payload = json.loads(data)
                    text = payload.get("text")
                except json.JSONDecodeError:
                    text = data
                if text:
                    log_entries.append(str(text))
                    if not _should_surface_runner_log(str(text)):
                        continue
                    msg = f"\n\n[log] {text}"
                    assistant_buffer += msg
                    had_output = True
                    await assistant_msg.stream_token(msg)
                    await _persist_partial_if_due()

            elif event_type == "runner.exit":
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    payload = {}
                reason = payload.get("reason")
                return_code = payload.get("return_code")
                if reason and reason != "completed":
                    msg = f"\n\n[runner] exited: {reason}"
                    if return_code is not None:
                        msg += f" (code {return_code})"
                    error_entries.append(msg.strip())
                    assistant_buffer += msg
                    had_output = True
                    await assistant_msg.stream_token(msg)
                    await _persist_partial_if_due()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response else None
        detail = _runner_http_error_detail(exc)
        if status_code == 429:
            msg = "[runner] Busy: request queue is full. Please retry shortly."
        elif status_code is not None:
            msg = f"[runner] Request failed with status {status_code}."
        else:
            msg = "[runner] Request failed."
        if detail:
            msg = f"{msg} {detail}"
        error_entries.append(msg)
        assistant_buffer += f"\n\n{msg}"
        had_output = True
        await assistant_msg.stream_token(f"\n\n{msg}")
        await _persist_partial_if_due(force=True)
    except httpx.HTTPError as exc:
        msg = f"[runner] Connection error: {exc}"
        error_entries.append(msg)
        assistant_buffer += f"\n\n{msg}"
        had_output = True
        await assistant_msg.stream_token(f"\n\n{msg}")
        await _persist_partial_if_due(force=True)
    finally:
        await _unregister_active_run(thread_id)
        await _mark_thread_stopped(thread_owner_keys, thread_id)
        thinking_end_payload: dict[str, str] = {
            "type": "assistant_thinking",
            "status": "end",
        }
        if thread_id:
            thinking_end_payload["thread_id"] = thread_id
        await cl.send_window_message(thinking_end_payload)
        if status_msg is not None:
            await status_msg.remove()

    if not had_output:
        assistant_msg.content = "No assistant output."
    else:
        assistant_msg.content = assistant_buffer
    await assistant_msg.update()
    history = _append_history(history, "user", message.content)
    history = _append_history(history, "assistant", assistant_msg.content)
    cl.user_session.set("chat_history", history)
    if active_package_id and selected_source != PACKAGE_SOURCE_MANUAL:
        cl.user_session.set(SESSION_PACKAGE_ID_KEY, active_package_id)
        if selected_source == PACKAGE_SOURCE_NONE:
            cl.user_session.set(SESSION_PACKAGE_SOURCE_KEY, PACKAGE_SOURCE_DEFAULT)
    await _attach_artifacts_from_text(assistant_buffer, session_id, assistant_msg)
    if TRANSPARENCY_ENABLED:
        created_files: list[str] = []
        modified_files: list[str] = []
        if repo_root is not None and snapshot_before is not None and repo_root.exists():
            snapshot_after = _snapshot_repo(repo_root)
            created_files, modified_files = _diff_snapshots(
                snapshot_before, snapshot_after
            )
        report = _format_transparency_report(
            active_package_id,
            _dedupe_preserve(tool_calls),
            _dedupe_preserve(commands_run),
            created_files,
            modified_files,
            log_entries,
            error_entries,
        )
        transparency_msg = cl.Message(content=report)
        await transparency_msg.send()
