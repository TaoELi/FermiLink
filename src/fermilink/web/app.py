import asyncio
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import aiofiles
from passlib.hash import pbkdf2_sha256
from fermilink.config import (
    resolve_fermilink_home,
    resolve_workspaces_root as resolve_default_workspaces_root,
)
from fermilink.runner.scientific_packages import (
    load_registry,
    normalize_package_id,
    resolve_scipkg_root,
)
from sqlalchemy.engine import make_url


app_root_raw = os.getenv("CHAINLIT_APP_ROOT")
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


_sync_chainlit_markdown()

if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = DEFAULT_DB_URL

_GENERATED_AUTH_SECRET = False
if "CHAINLIT_AUTH_SECRET" not in os.environ:
    os.environ["CHAINLIT_AUTH_SECRET"] = secrets.token_urlsafe(32)
    _GENERATED_AUTH_SECRET = True

import chainlit as cl
import httpx
from chainlit.config import config, public_dir
from chainlit.context import context as chainlit_context
from chainlit.data import get_data_layer
from chainlit.data.storage_clients.base import BaseStorageClient
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.server import app as chainlit_fastapi_app
from fastapi import Body, HTTPException

RUNNER_URL = os.getenv("RUNNER_URL", "http://runner:8000")
RUNNER_METRICS_TOKEN = os.getenv("RUNNER_METRICS_TOKEN", "").strip()
DB_URL = os.getenv("DATABASE_URL", DEFAULT_DB_URL)
AUTH_DB_URL = os.getenv("AUTH_DB_URL", DEFAULT_AUTH_DB_URL)
LOGGER = logging.getLogger(__name__)
AUTH_AUTO_REGISTER = os.getenv("AUTH_AUTO_REGISTER", "false").strip().lower() in {
    "1",
    "true",
    "yes",
}
AUTH_SIGNUP_ENABLED = os.getenv("AUTH_SIGNUP_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
}
try:
    AUTH_MIN_PASSWORD_LEN = int(os.getenv("AUTH_MIN_PASSWORD_LEN", "8"))
except ValueError:
    AUTH_MIN_PASSWORD_LEN = 8
    LOGGER.warning("Invalid AUTH_MIN_PASSWORD_LEN value. Falling back to 8.")
try:
    AUTH_MAX_USERS = int(os.getenv("AUTH_MAX_USERS", "0"))
except ValueError:
    AUTH_MAX_USERS = 0
    LOGGER.warning("Invalid AUTH_MAX_USERS value. Falling back to 0 (unlimited).")
if AUTH_MAX_USERS < 0:
    LOGGER.warning("AUTH_MAX_USERS=%s is invalid. Using 0 (unlimited).", AUTH_MAX_USERS)
    AUTH_MAX_USERS = 0
if _GENERATED_AUTH_SECRET:
    LOGGER.warning(
        "CHAINLIT_AUTH_SECRET was not set. Generated a temporary secret; "
        "users will be logged out after restart."
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
    os.getenv("PROMPT_DEFAULT_GROUP", "average").strip().lower() or "average"
)
PROMPT_LIMITS = {
    "average": _get_int_env("PROMPT_LIMIT_AVERAGE", 100),
    "star": _get_int_env("PROMPT_LIMIT_STAR", 100),
}
PROMPT_DAY_TZ = os.getenv("PROMPT_DAY_TZ", "UTC").strip() or "UTC"


ARTIFACT_PREFIXES = tuple(
    prefix.strip()
    for prefix in os.getenv(
        "CHAINLIT_ARTIFACT_PREFIXES", ",".join(DEFAULT_ARTIFACT_PREFIXES)
    ).split(",")
    if prefix.strip()
)
MAX_ATTACHMENT_BYTES = _get_int_env("CHAINLIT_MAX_ATTACHMENT_BYTES", 50 * 1024 * 1024)
ZIP_MIN_COUNT = _get_int_env("CHAINLIT_ZIP_MIN_COUNT", 3)
MAX_PROMPT_CHARS = _get_int_env("RUNNER_MAX_PROMPT_CHARS", DEFAULT_MAX_PROMPT_CHARS)
HISTORY_MAX_MESSAGES = _get_int_env("CHAINLIT_HISTORY_MAX_MESSAGES", 40)
HISTORY_MAX_CHARS = _get_int_env("CHAINLIT_HISTORY_MAX_CHARS", 40_000)
HISTORY_ENTRY_MAX_CHARS = _get_int_env("CHAINLIT_HISTORY_ENTRY_CHARS", 4_000)
TRANSPARENCY_ENABLED = os.getenv(
    "CHAINLIT_TRANSPARENCY_ENABLED", "false"
).strip().lower() in {"1", "true", "yes"}
TRANSPARENCY_MAX_ITEMS = _get_int_env("CHAINLIT_TRANSPARENCY_MAX_ITEMS", 200)
TRANSPARENCY_MAX_LOG_ENTRIES = _get_int_env("CHAINLIT_TRANSPARENCY_MAX_LOG_ENTRIES", 50)
TRANSPARENCY_MAX_ENTRY_CHARS = _get_int_env(
    "CHAINLIT_TRANSPARENCY_MAX_ENTRY_CHARS", 500
)
FORWARD_RUNNER_LOGS = _get_bool_env("CHAINLIT_FORWARD_RUNNER_LOGS", False)
PACKAGE_ROUTER_ENABLED = _get_bool_env("CHAINLIT_PACKAGE_ROUTER_ENABLED", True)
PACKAGE_ROUTER_AUTO_DEFAULT = _get_bool_env("CHAINLIT_PACKAGE_ROUTER_AUTO", True)
PACKAGE_ROUTER_STICKY = _get_bool_env("CHAINLIT_PACKAGE_ROUTER_STICKY", True)
PACKAGE_ROUTER_MIN_SCORE = _get_int_env("CHAINLIT_PACKAGE_ROUTER_MIN_SCORE", 2)
PACKAGE_ROUTER_MIN_MARGIN = _get_int_env("CHAINLIT_PACKAGE_ROUTER_MIN_MARGIN", 1)
PACKAGE_ROUTER_SWITCH_MARGIN = _get_int_env("CHAINLIT_PACKAGE_ROUTER_SWITCH_MARGIN", 2)
PACKAGE_SECOND_GUESS_ENABLED = _get_bool_env(
    "CHAINLIT_PACKAGE_SECOND_GUESS_ENABLED", True
)
PACKAGE_SECOND_GUESS_MIN_CONFIDENCE = _get_float_env(
    "CHAINLIT_PACKAGE_SECOND_GUESS_MIN_CONFIDENCE", 0.75
)
PACKAGE_SECOND_GUESS_TIMEOUT_SECONDS = _get_float_env(
    "CHAINLIT_PACKAGE_SECOND_GUESS_TIMEOUT_SECONDS", 25.0
)
STREAM_PARTIAL_PERSIST_SECONDS = max(
    0.0, _get_float_env("CHAINLIT_STREAM_PARTIAL_PERSIST_SECONDS", 1.0)
)
ADMISSION_POLL_INTERVAL_SECONDS = max(
    0.1, _get_float_env("CHAINLIT_ADMISSION_POLL_INTERVAL_SECONDS", 0.5)
)
ADMISSION_POLL_TIMEOUT_SECONDS = max(
    0.0, _get_float_env("CHAINLIT_ADMISSION_POLL_TIMEOUT_SECONDS", 0.0)
)
PACKAGE_ROUTER_RULES_FILENAME = (
    os.getenv("CHAINLIT_PACKAGE_ROUTER_RULES", "router_rules.json").strip()
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
PACKAGE_FAMILY_RULES: dict[str, dict[str, list[str]]] = {
    "maxwelllink": {
        "strong_keywords": [
            "maxwelllink",
            "light matter",
            "maxwell bloch",
            "quantum optics",
            "radiative decay",
            "driven two level",
            "spontaneous emission",
            "two level system",
            "two-level system",
            "weakly excited",
            "quantum emitter",
        ],
        "keywords": [
            "electromagnetic solver",
            "open quantum",
            "photonics",
            "maxwell equation",
            "coupled light",
            "population dynamics",
            "density matrix",
            "dephasing",
            "purcell",
            "vacuum coupling",
        ],
        "negative_keywords": ["classical md", "force field"],
    },
    "meep": {
        "strong_keywords": [
            "meep",
            "fdtd",
            "finite difference time domain",
            "electromagnetic wave propagation",
        ],
        "keywords": [
            "dielectric",
            "waveguide",
            "photonic crystal",
            "pml",
            "harminv",
        ],
        "negative_keywords": [
            "gaussian",
            "qchem",
            "lammps",
            "gromacs",
            "spontaneous emission",
            "two level system",
            "two-level system",
            "weakly excited",
            "density matrix",
            "population dynamics",
            "maxwell bloch",
        ],
    },
    "qchem": {
        "strong_keywords": [
            "qchem",
            "q-chem",
            "electronic structure",
            "dft",
            "ab initio",
            "hartree fock",
        ],
        "keywords": [
            "basis set",
            "scf",
            "td-dft",
            "coupled cluster",
            "quantum chemistry",
        ],
        "negative_keywords": ["md", "gromacs", "lammps", "fdtd"],
    },
    "gaussian": {
        "strong_keywords": [
            "gaussian",
            "gaussian16",
            "g16",
            "electronic structure",
            "quantum chemistry",
        ],
        "keywords": ["basis set", "opt freq", "pcm", "scf", "dft"],
        "negative_keywords": ["md", "lammps", "gromacs", "fdtd"],
    },
    "lammps": {
        "strong_keywords": [
            "lammps",
            "classical md",
            "molecular dynamics",
            "force field",
        ],
        "keywords": ["pair style", "thermo", "nvt", "npt", "dump"],
        "negative_keywords": ["td-dft", "gaussian", "qchem", "fdtd"],
    },
    "gromacs": {
        "strong_keywords": [
            "gromacs",
            "mdp",
            "gmx",
            "classical md",
            "molecular dynamics",
        ],
        "keywords": ["topol", "gro", "xtc", "nvt", "npt"],
        "negative_keywords": ["td-dft", "gaussian", "qchem", "fdtd"],
    },
}


def _normalize_package_id_safe(value: str | None) -> str | None:
    """Normalize package id and suppress validation exceptions.

    Parameters
    ----------
    value : str or None
        Raw package id.

    Returns
    -------
    str or None
        Normalized id when valid, otherwise `None`.
    """

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return normalize_package_id(value)
    except Exception:
        return None


def _dedupe_terms(values: list[str]) -> list[str]:
    """Deduplicate string terms while preserving order.

    Parameters
    ----------
    values : list of str
        Input sequence.

    Returns
    -------
    list of str
        Unique normalized terms.
    """

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        term = value.strip().lower()
        if not term or term in seen:
            continue
        seen.add(term)
        result.append(term)
    return result


def _normalize_rule_terms(raw: Any) -> list[str]:
    """Normalize router term payload to a list of unique strings.

    Parameters
    ----------
    raw : Any
        Raw value from config.

    Returns
    -------
    list of str
        Normalized terms.
    """

    if isinstance(raw, str):
        return _dedupe_terms(raw.split(","))
    if isinstance(raw, list):
        return _dedupe_terms([item for item in raw if isinstance(item, str)])
    return []


def _resolve_package_registry() -> tuple[list[str], str | None, Path]:
    """Load installed package ids and active package from registry.

    Returns
    -------
    tuple
        `(package_ids, active_package_id, scipkg_root)`.
    """

    scipkg_root = APP_ROOT / "scientific_packages"
    try:
        scipkg_root = resolve_scipkg_root()
        registry = load_registry(scipkg_root)
    except Exception as exc:
        LOGGER.warning("Failed to load scientific package registry: %s", exc)
        return [], None, scipkg_root

    packages = registry.get("packages", {})
    package_ids: list[str] = []
    if isinstance(packages, dict):
        for raw_id in packages.keys():
            normalized = _normalize_package_id_safe(str(raw_id))
            if normalized:
                package_ids.append(normalized)
    package_ids = sorted(set(package_ids))

    active_raw = registry.get("active_package")
    active_package_id = (
        _normalize_package_id_safe(active_raw) if isinstance(active_raw, str) else None
    )
    if active_package_id not in package_ids:
        active_package_id = None
    return package_ids, active_package_id, scipkg_root


def _load_router_config(scipkg_root: Path) -> dict[str, Any]:
    """Load package-router config from `scientific_packages/router_rules.json`.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root.

    Returns
    -------
    dict[str, Any]
        Parsed config with defaults when the file is missing/invalid.
    """

    default_config: dict[str, Any] = {
        "default_package_id": None,
        "min_score": PACKAGE_ROUTER_MIN_SCORE,
        "min_margin": PACKAGE_ROUTER_MIN_MARGIN,
        "packages": {},
    }
    path = scipkg_root / PACKAGE_ROUTER_RULES_FILENAME
    if not path.is_file():
        return default_config
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("Failed to read router rules %s: %s", path, exc)
        return default_config
    if not isinstance(loaded, dict):
        return default_config

    config = dict(default_config)
    default_id = _normalize_package_id_safe(loaded.get("default_package_id"))
    if default_id:
        config["default_package_id"] = default_id

    min_score_raw = loaded.get("min_score")
    if isinstance(min_score_raw, int):
        config["min_score"] = min_score_raw

    min_margin_raw = loaded.get("min_margin")
    if isinstance(min_margin_raw, int):
        config["min_margin"] = min_margin_raw

    packages_raw = loaded.get("packages")
    if isinstance(packages_raw, dict):
        normalized_packages: dict[str, Any] = {}
        for raw_id, payload in packages_raw.items():
            if not isinstance(payload, dict):
                continue
            normalized_id = _normalize_package_id_safe(str(raw_id))
            if not normalized_id:
                continue
            normalized_packages[normalized_id] = payload
        config["packages"] = normalized_packages
    return config


def _package_id_terms(package_id: str) -> list[str]:
    """Build fallback match terms from a package id.

    Parameters
    ----------
    package_id : str
        Normalized package id.

    Returns
    -------
    list of str
        Match terms.
    """

    lowered = package_id.lower()
    terms = [
        lowered,
        lowered.replace("-", " "),
        lowered.replace("_", " "),
        lowered.replace("_", "-"),
        lowered.replace("-", "_"),
    ]
    parts = re.split(r"[-_]+", lowered)
    for part in parts:
        if len(part) >= 4 and not part.isdigit():
            terms.append(part)
    return _dedupe_terms(terms)


def _build_package_rule(
    package_id: str, config_packages: dict[str, Any]
) -> dict[str, list[str]]:
    """Build effective router rule for one installed package.

    Parameters
    ----------
    package_id : str
        Installed package id.
    config_packages : dict[str, Any]
        Configured package-specific rules.

    Returns
    -------
    dict[str, list[str]]
        Rule payload with `keywords`, `strong_keywords`, and `negative_keywords`.
    """

    base_keywords = _package_id_terms(package_id)
    base_strong: list[str] = []
    base_negative: list[str] = []

    lowered = package_id.lower()
    for family, payload in PACKAGE_FAMILY_RULES.items():
        if family not in lowered:
            continue
        base_keywords.extend(payload.get("keywords", []))
        base_strong.extend(payload.get("strong_keywords", []))
        base_negative.extend(payload.get("negative_keywords", []))

    configured = config_packages.get(package_id)
    configured_keywords: list[str] = []
    configured_strong: list[str] = []
    configured_negative: list[str] = []
    if isinstance(configured, dict):
        configured_keywords = _normalize_rule_terms(configured.get("keywords"))
        configured_strong = _normalize_rule_terms(configured.get("strong_keywords"))
        configured_negative = _normalize_rule_terms(configured.get("negative_keywords"))

    return {
        "keywords": _dedupe_terms(base_keywords + configured_keywords),
        "strong_keywords": _dedupe_terms(base_strong + configured_strong),
        "negative_keywords": _dedupe_terms(base_negative + configured_negative),
    }


def _match_term_count(text: str, terms: list[str]) -> int:
    """Count unique matched terms in normalized text.

    Parameters
    ----------
    text : str
        Lowercase prompt text.
    terms : list of str
        Candidate terms.

    Returns
    -------
    int
        Number of matched terms.
    """

    hits = 0
    for term in terms:
        if not term:
            continue
        if re.fullmatch(r"[a-z0-9]+", term):
            pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
            if re.search(pattern, text):
                hits += 1
        elif term in text:
            hits += 1
    return hits


def _route_package_candidate(
    user_text: str,
    package_ids: list[str],
    current_package_id: str | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Route one user request to the best package candidate.

    Parameters
    ----------
    user_text : str
        Current user message.
    package_ids : list of str
        Installed package ids.
    current_package_id : str or None
        Existing selected package for this chat.
    config : dict[str, Any]
        Router configuration.

    Returns
    -------
    dict[str, Any]
        Routing decision including selected package and score diagnostics.
    """

    text = (user_text or "").strip().lower()
    if not text:
        return {
            "selected_package_id": None,
            "reason": "empty_prompt",
            "scores": [],
            "margin": 0,
        }
    if not package_ids:
        return {
            "selected_package_id": None,
            "reason": "no_packages",
            "scores": [],
            "margin": 0,
        }

    config_packages = config.get("packages", {})
    if not isinstance(config_packages, dict):
        config_packages = {}

    scored: list[dict[str, Any]] = []
    for package_id in package_ids:
        rule = _build_package_rule(package_id, config_packages)
        strong_hits = _match_term_count(text, rule["strong_keywords"])
        keyword_hits = _match_term_count(text, rule["keywords"])
        negative_hits = _match_term_count(text, rule["negative_keywords"])

        score = strong_hits * 3 + keyword_hits - negative_hits * 2
        if package_id in text:
            score += 3

        scored.append(
            {
                "package_id": package_id,
                "score": score,
                "strong_hits": strong_hits,
                "keyword_hits": keyword_hits,
                "negative_hits": negative_hits,
            }
        )

    scored.sort(key=lambda item: (item["score"], item["package_id"]), reverse=True)
    top = scored[0]
    second = scored[1] if len(scored) > 1 else None

    min_score_raw = config.get("min_score", PACKAGE_ROUTER_MIN_SCORE)
    min_margin_raw = config.get("min_margin", PACKAGE_ROUTER_MIN_MARGIN)
    min_score = int(min_score_raw) if isinstance(min_score_raw, int) else 0
    min_margin = int(min_margin_raw) if isinstance(min_margin_raw, int) else 0

    margin = top["score"] - second["score"] if second else top["score"]
    if top["score"] < min_score:
        return {
            "selected_package_id": None,
            "reason": "low_score",
            "scores": scored,
            "margin": margin,
        }

    if second is not None and margin < min_margin:
        if current_package_id and current_package_id == top["package_id"]:
            return {
                "selected_package_id": current_package_id,
                "reason": "ambiguous_keep_current",
                "scores": scored,
                "margin": margin,
            }
        return {
            "selected_package_id": None,
            "reason": "ambiguous",
            "scores": scored,
            "margin": margin,
        }

    return {
        "selected_package_id": top["package_id"],
        "reason": "matched",
        "scores": scored,
        "margin": margin,
    }


def _resolve_default_package_id(
    package_ids: list[str],
    active_package_id: str | None,
    config: dict[str, Any],
) -> str | None:
    """Resolve default package from config, registry active package, or installed list.

    Parameters
    ----------
    package_ids : list of str
        Installed package ids.
    active_package_id : str or None
        Registry active package.
    config : dict[str, Any]
        Router config.

    Returns
    -------
    str or None
        Default package id.
    """

    if not package_ids:
        return None
    package_set = set(package_ids)
    config_default = _normalize_package_id_safe(config.get("default_package_id"))
    if config_default in package_set:
        return config_default
    if active_package_id in package_set:
        return active_package_id
    return package_ids[0]


def _build_package_catalog(
    package_ids: list[str], active_package_id: str | None, scipkg_root: Path
) -> list[dict[str, Any]]:
    """Build concise package catalog for preflight routing checks.

    Parameters
    ----------
    package_ids : list of str
        Installed package ids.
    active_package_id : str or None
        Registry active package id.
    scipkg_root : Path
        Scientific package root.

    Returns
    -------
    list[dict[str, Any]]
        Package metadata summary.
    """

    catalog: list[dict[str, Any]] = []
    packages_payload: dict[str, Any] = {}
    try:
        registry = load_registry(scipkg_root)
        maybe_packages = registry.get("packages", {})
        if isinstance(maybe_packages, dict):
            packages_payload = maybe_packages
    except Exception as exc:
        LOGGER.warning("Failed to load package catalog metadata: %s", exc)
        packages_payload = {}

    for package_id in package_ids:
        meta_raw = packages_payload.get(package_id)
        meta = meta_raw if isinstance(meta_raw, dict) else {}
        item: dict[str, Any] = {
            "id": package_id,
            "active": package_id == active_package_id,
        }

        title = meta.get("title")
        if isinstance(title, str) and title and title != package_id:
            item["title"] = title

        source = meta.get("source")
        if isinstance(source, str) and source:
            item["source"] = source

        overlay_entries = meta.get("overlay_entries")
        if isinstance(overlay_entries, list):
            normalized_entries = [
                str(entry).strip()
                for entry in overlay_entries
                if isinstance(entry, str) and str(entry).strip()
            ]
            if normalized_entries:
                item["overlay_entries"] = normalized_entries

        dependency_package_ids = meta.get("dependency_package_ids")
        if isinstance(dependency_package_ids, list):
            normalized_dependency_ids = [
                str(entry).strip()
                for entry in dependency_package_ids
                if isinstance(entry, str) and str(entry).strip()
            ]
            if normalized_dependency_ids:
                item["dependency_package_ids"] = normalized_dependency_ids

        catalog.append(item)
    return catalog


def _build_second_guess_prompt(
    *,
    user_text: str,
    current_package_id: str | None,
    package_catalog: list[dict[str, Any]],
) -> str:
    """Create routing preflight prompt for Codex second-guess decision.

    Parameters
    ----------
    user_text : str
        Current user message.
    current_package_id : str or None
        Initial package from keyword/default router.
    package_catalog : list of dict
        Installed package summary.

    Returns
    -------
    str
        Prompt text requesting strict JSON output.
    """

    catalog_json = json.dumps(package_catalog, indent=2, ensure_ascii=False)
    current_label = current_package_id if current_package_id else "none"
    return (
        "PACKAGE ROUTING PREFLIGHT ONLY.\n"
        "You must decide whether the currently selected scientific package is suitable.\n"
        "Read AGENTS.md in the repo root and follow its Package Routing Policy.\n"
        "Do NOT run shell commands. Do NOT edit files. Do NOT create outputs.\n"
        "Return exactly one JSON object and nothing else.\n\n"
        "Required JSON schema:\n"
        "{\n"
        '  "route": "keep" | "switch",\n'
        '  "package_id": "<installed_package_id_or_null>",\n'
        '  "confidence": <number_between_0_and_1>,\n'
        '  "reason": "<short_reason>"\n'
        "}\n\n"
        f"Current package: {current_label}\n"
        f"Installed package catalog:\n{catalog_json}\n\n"
        f"User request:\n{(user_text or '').strip()}\n"
    )


def _extract_first_json_object(text: str) -> dict[str, Any] | None:
    """Extract first valid JSON object from text.

    Parameters
    ----------
    text : str
        Raw model output.

    Returns
    -------
    dict[str, Any] or None
        Parsed JSON object when found.
    """

    if not text:
        return None

    start_positions = [idx for idx, char in enumerate(text) if char == "{"]
    for start in start_positions:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : index + 1]
                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
                if depth < 0:
                    break
    return None


def _coerce_confidence(value: Any) -> float:
    """Normalize confidence value to [0, 1].

    Parameters
    ----------
    value : Any
        Raw confidence field.

    Returns
    -------
    float
        Confidence value in range [0, 1].
    """

    if isinstance(value, (int, float)):
        confidence = float(value)
    elif isinstance(value, str):
        try:
            confidence = float(value.strip())
        except ValueError:
            return 0.0
    else:
        return 0.0

    if confidence < 0:
        return 0.0
    if confidence > 1:
        return 1.0
    return confidence


async def _run_package_second_guess(
    *,
    user_text: str,
    session_id: str | None,
    user_id: str | None,
    selected_package_id: str | None,
    selected_source: str,
) -> dict[str, Any]:
    """Run AGENTS-guided preflight package routing check.

    Parameters
    ----------
    user_text : str
        Current user request.
    session_id : str or None
        Current chat session id.
    user_id : str or None
        Authenticated user identifier for runner-side concurrency control.
    selected_package_id : str or None
        Initial package from first-pass routing.
    selected_source : str
        Current source label for the selection.

    Returns
    -------
    dict[str, Any]
        Routing result containing final package, switch flag, and notes.
    """

    if not PACKAGE_SECOND_GUESS_ENABLED:
        return {
            "package_id": selected_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": session_id,
            "consulted": False,
            "note": "second_guess_disabled",
        }

    if selected_source == PACKAGE_SOURCE_MANUAL:
        return {
            "package_id": selected_package_id,
            "source": PACKAGE_SOURCE_MANUAL,
            "switched": False,
            "session_id": session_id,
            "consulted": False,
            "note": "manual_pin",
        }

    package_ids, active_package_id, scipkg_root = _resolve_package_registry()
    if len(package_ids) < 2:
        return {
            "package_id": selected_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": session_id,
            "consulted": False,
            "note": "insufficient_packages",
        }

    package_set = set(package_ids)
    config = _load_router_config(scipkg_root)

    base_package_id = (
        selected_package_id
        if isinstance(selected_package_id, str) and selected_package_id in package_set
        else _resolve_default_package_id(package_ids, active_package_id, config)
    )

    if not base_package_id:
        return {
            "package_id": selected_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": session_id,
            "consulted": False,
            "note": "no_base_package",
        }

    package_catalog = _build_package_catalog(
        package_ids=package_ids,
        active_package_id=active_package_id,
        scipkg_root=scipkg_root,
    )
    preflight_prompt = _build_second_guess_prompt(
        user_text=user_text,
        current_package_id=base_package_id,
        package_catalog=package_catalog,
    )

    payload: dict[str, Any] = {
        "session_id": session_id,
        "user_prompt": preflight_prompt,
        "sandbox": "read-only",
        "package_id": base_package_id,
    }
    if isinstance(user_id, str) and user_id.strip():
        payload["user_id"] = user_id

    resolved_session_id = session_id
    assistant_chunks: list[str] = []

    async def _collect() -> None:
        nonlocal resolved_session_id
        async for event_type, data in _stream_runner(payload):
            if event_type == "meta":
                try:
                    meta = json.loads(data)
                except json.JSONDecodeError:
                    continue
                maybe_session = meta.get("session_id")
                if isinstance(maybe_session, str) and maybe_session:
                    resolved_session_id = maybe_session
                continue

            if event_type != "codex":
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            item = event.get("item")
            if not isinstance(item, dict):
                item = {}
            item_type = item.get("type") or event.get("type") or ""
            if not isinstance(item_type, str):
                continue
            if not item_type.startswith("agent_message"):
                continue
            text = _extract_text(event) or ""
            if text:
                assistant_chunks.append(text)

    try:
        timeout = PACKAGE_SECOND_GUESS_TIMEOUT_SECONDS
        if timeout > 0:
            await asyncio.wait_for(_collect(), timeout=timeout)
        else:
            await _collect()
    except asyncio.TimeoutError:
        return {
            "package_id": base_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": resolved_session_id,
            "consulted": True,
            "note": "second_guess_timeout",
        }
    except Exception as exc:
        LOGGER.warning("Second-guess preflight failed: %s", exc)
        return {
            "package_id": base_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": resolved_session_id,
            "consulted": True,
            "note": f"second_guess_error:{exc}",
        }

    raw_text = "".join(assistant_chunks).strip()
    decision = _extract_first_json_object(raw_text)
    if not isinstance(decision, dict):
        return {
            "package_id": base_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": resolved_session_id,
            "consulted": True,
            "note": "second_guess_invalid_json",
        }

    route_raw = decision.get("route")
    route = str(route_raw).strip().lower() if route_raw is not None else ""
    suggested_package = _normalize_package_id_safe(decision.get("package_id"))
    confidence = _coerce_confidence(decision.get("confidence"))
    reason_raw = decision.get("reason")
    reason = str(reason_raw).strip() if isinstance(reason_raw, str) else ""
    reason_short = reason[:240] if reason else ""

    if route not in {"keep", "switch"}:
        return {
            "package_id": base_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": resolved_session_id,
            "consulted": True,
            "note": "second_guess_invalid_route",
        }

    if route == "keep":
        note = (
            f"second_guess_keep(conf={confidence:.2f}, reason={reason_short})"
            if reason_short
            else f"second_guess_keep(conf={confidence:.2f})"
        )
        return {
            "package_id": base_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": resolved_session_id,
            "consulted": True,
            "note": note,
        }

    if suggested_package not in package_set:
        return {
            "package_id": base_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": resolved_session_id,
            "consulted": True,
            "note": "second_guess_invalid_target",
        }

    if suggested_package == base_package_id:
        return {
            "package_id": base_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": resolved_session_id,
            "consulted": True,
            "note": "second_guess_same_target",
        }

    if confidence < PACKAGE_SECOND_GUESS_MIN_CONFIDENCE:
        return {
            "package_id": base_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": resolved_session_id,
            "consulted": True,
            "note": f"second_guess_low_confidence({confidence:.2f})",
        }

    note = (
        f"second_guess_switch({base_package_id}->{suggested_package}, "
        f"conf={confidence:.2f}, reason={reason_short})"
        if reason_short
        else f"second_guess_switch({base_package_id}->{suggested_package}, conf={confidence:.2f})"
    )
    return {
        "package_id": suggested_package,
        "source": PACKAGE_SOURCE_SECOND_GUESS,
        "switched": True,
        "session_id": resolved_session_id,
        "consulted": True,
        "note": note,
    }


def _resolve_package_alias(raw_target: str, package_ids: list[str]) -> str | None:
    """Resolve flexible package id input to one installed package id.

    Parameters
    ----------
    raw_target : str
        User-provided package identifier.
    package_ids : list of str
        Installed package ids.

    Returns
    -------
    str or None
        Resolved package id when unambiguous.
    """

    normalized = _normalize_package_id_safe(raw_target)
    if normalized and normalized in package_ids:
        return normalized

    lowered = (raw_target or "").strip().lower()
    if not lowered:
        return None

    exact_matches = [package_id for package_id in package_ids if package_id == lowered]
    if len(exact_matches) == 1:
        return exact_matches[0]

    prefix_matches = [
        package_id for package_id in package_ids if package_id.startswith(lowered)
    ]
    if len(prefix_matches) == 1:
        return prefix_matches[0]

    contains_matches = [
        package_id for package_id in package_ids if lowered in package_id
    ]
    if len(contains_matches) == 1:
        return contains_matches[0]
    return None


def _get_session_auto_route_flag() -> bool:
    """Return session auto-route setting with default initialization.

    Returns
    -------
    bool
        Whether auto-routing is enabled for this chat session.
    """

    value = cl.user_session.get(SESSION_PACKAGE_AUTO_KEY)
    if isinstance(value, bool):
        return value
    cl.user_session.set(SESSION_PACKAGE_AUTO_KEY, PACKAGE_ROUTER_AUTO_DEFAULT)
    return PACKAGE_ROUTER_AUTO_DEFAULT


def _resolve_package_for_turn(user_text: str) -> dict[str, Any]:
    """Resolve package selection for the current user message.

    Parameters
    ----------
    user_text : str
        Current user message text.

    Returns
    -------
    dict[str, Any]
        Selection payload with package id, source, and reason.
    """

    package_ids, active_package_id, scipkg_root = _resolve_package_registry()
    config = _load_router_config(scipkg_root)
    package_set = set(package_ids)
    resolution_context = {
        "scipkg_root": str(scipkg_root),
        "installed_package_count": len(package_ids),
    }

    current_package_id = cl.user_session.get(SESSION_PACKAGE_ID_KEY)
    if isinstance(current_package_id, str):
        current_package_id = _normalize_package_id_safe(current_package_id)
    else:
        current_package_id = None
    current_source = cl.user_session.get(SESSION_PACKAGE_SOURCE_KEY)
    if not isinstance(current_source, str) or not current_source:
        current_source = PACKAGE_SOURCE_NONE

    if current_package_id not in package_set:
        current_package_id = None
        current_source = PACKAGE_SOURCE_NONE

    if current_source == PACKAGE_SOURCE_MANUAL and current_package_id:
        return {
            "package_id": current_package_id,
            "source": PACKAGE_SOURCE_MANUAL,
            "reason": "manual_pin",
            "changed": False,
            **resolution_context,
        }

    if not package_ids:
        return {
            "package_id": None,
            "source": PACKAGE_SOURCE_NONE,
            "reason": "no_packages",
            "changed": current_package_id is not None,
            **resolution_context,
        }

    auto_enabled = _get_session_auto_route_flag() and PACKAGE_ROUTER_ENABLED
    if auto_enabled:
        decision = _route_package_candidate(
            user_text=user_text,
            package_ids=package_ids,
            current_package_id=current_package_id,
            config=config,
        )
        candidate = decision.get("selected_package_id")
        if isinstance(candidate, str) and candidate in package_set:
            if (
                PACKAGE_ROUTER_STICKY
                and current_package_id
                and current_package_id != candidate
                and int(decision.get("margin", 0)) < PACKAGE_ROUTER_SWITCH_MARGIN
            ):
                return {
                    "package_id": current_package_id,
                    "source": current_source or PACKAGE_SOURCE_AUTO,
                    "reason": "sticky_keep_current",
                    "changed": False,
                    **resolution_context,
                }
            return {
                "package_id": candidate,
                "source": PACKAGE_SOURCE_AUTO,
                "reason": decision.get("reason", "matched"),
                "changed": candidate != current_package_id
                or current_source != PACKAGE_SOURCE_AUTO,
                **resolution_context,
            }

    if current_package_id:
        return {
            "package_id": current_package_id,
            "source": current_source or PACKAGE_SOURCE_DEFAULT,
            "reason": "keep_current",
            "changed": False,
            **resolution_context,
        }

    fallback = _resolve_default_package_id(package_ids, active_package_id, config)
    return {
        "package_id": fallback,
        "source": PACKAGE_SOURCE_DEFAULT if fallback else PACKAGE_SOURCE_NONE,
        "reason": "default_fallback" if fallback else "no_default",
        "changed": bool(fallback),
        **resolution_context,
    }


def _parse_package_command(content: str) -> tuple[str, list[str]] | None:
    """Parse `/package` chat commands.

    Parameters
    ----------
    content : str
        User message content.

    Returns
    -------
    tuple[str, list[str]] or None
        Command action and args, or `None` when not a package command.
    """

    text = (content or "").strip()
    if not text:
        return None
    if not text.lower().startswith(PACKAGE_COMMAND_PREFIX):
        return None

    parts = text.split()
    if len(parts) == 1:
        return "help", []

    subcommand = parts[1].strip().lower()
    args = parts[2:]
    if subcommand in {"list", "ls"}:
        return "list", args
    if subcommand in {"current", "status"}:
        return "current", args
    if subcommand in {"use", "set"}:
        return "use", args
    if subcommand in {"clear", "reset"}:
        return "clear", args
    if subcommand == "auto":
        return "auto", args
    if subcommand == "help":
        return "help", args

    # Support shorthand: `/package <package-id>`
    return "use", parts[1:]


def _format_package_list(
    package_ids: list[str],
    active_package_id: str | None,
    current_package_id: str | None,
) -> str:
    """Format installed package list for chat output.

    Parameters
    ----------
    package_ids : list of str
        Installed package ids.
    active_package_id : str or None
        Registry active package id.
    current_package_id : str or None
        Current chat-selected package id.

    Returns
    -------
    str
        Markdown text list.
    """

    if not package_ids:
        return "No installed scientific packages found."

    lines = ["Installed packages:"]
    for package_id in package_ids:
        labels: list[str] = []
        if package_id == current_package_id:
            labels.append("current")
        if package_id == active_package_id:
            labels.append("registry-active")
        suffix = f" ({', '.join(labels)})" if labels else ""
        lines.append(f"- `{package_id}`{suffix}")
    return "\n".join(lines)


async def _handle_package_command(message: cl.Message) -> bool:
    """Handle `/package` command messages.

    Parameters
    ----------
    message : cl.Message
        Incoming user message.

    Returns
    -------
    bool
        `True` when the message was handled as a package command.
    """

    parsed = _parse_package_command(message.content)
    if parsed is None:
        return False
    action, args = parsed

    package_ids, active_package_id, _ = _resolve_package_registry()
    package_set = set(package_ids)
    current_package_id = cl.user_session.get(SESSION_PACKAGE_ID_KEY)
    if isinstance(current_package_id, str):
        current_package_id = _normalize_package_id_safe(current_package_id)
    else:
        current_package_id = None
    if current_package_id not in package_set:
        current_package_id = None

    current_source = cl.user_session.get(SESSION_PACKAGE_SOURCE_KEY)
    if not isinstance(current_source, str) or not current_source:
        current_source = PACKAGE_SOURCE_NONE
    auto_route = _get_session_auto_route_flag()

    if action == "help":
        await cl.Message(
            content=(
                "**Package Commands**\n"
                "- `/package list`: list installed packages\n"
                "- `/package current`: show current package for this chat\n"
                "- `/package use <package_id>`: pin package for this chat\n"
                "- `/package auto on|off`: enable/disable auto routing\n"
                "- `/package clear`: clear manual pin/current selection"
            )
        ).send()
        return True

    if action == "list":
        await cl.Message(
            content=_format_package_list(
                package_ids, active_package_id, current_package_id
            )
        ).send()
        return True

    if action == "current":
        current_label = current_package_id or "none"
        await cl.Message(
            content=(
                f"Current package: `{current_label}`\n"
                f"Selection source: `{current_source}`\n"
                f"Auto routing: `{'on' if auto_route else 'off'}`"
            )
        ).send()
        return True

    if action == "auto":
        if not args:
            await cl.Message(
                content=f"Auto routing is currently `{'on' if auto_route else 'off'}`."
            ).send()
            return True

        option = args[0].strip().lower()
        if option in {"on", "true", "1", "yes"}:
            cl.user_session.set(SESSION_PACKAGE_AUTO_KEY, True)
            await cl.Message(
                content=(
                    "Auto routing enabled for this chat. "
                    "Manual `/package use ...` pin still takes precedence."
                )
            ).send()
            return True
        if option in {"off", "false", "0", "no"}:
            cl.user_session.set(SESSION_PACKAGE_AUTO_KEY, False)
            await cl.Message(content="Auto routing disabled for this chat.").send()
            return True
        await cl.Message(
            content="Usage: `/package auto on` or `/package auto off`"
        ).send()
        return True

    if action == "clear":
        cl.user_session.set(SESSION_PACKAGE_ID_KEY, None)
        cl.user_session.set(SESSION_PACKAGE_SOURCE_KEY, PACKAGE_SOURCE_NONE)
        await cl.Message(
            content=(
                "Cleared current package selection for this chat. "
                "Next request will use auto/default routing."
            )
        ).send()
        return True

    if action == "use":
        target_raw = " ".join(args).strip() if args else ""
        if not target_raw:
            await cl.Message(content="Usage: `/package use <package_id>`").send()
            return True
        resolved = _resolve_package_alias(target_raw, package_ids)
        if not resolved:
            available = ", ".join(f"`{item}`" for item in package_ids) or "none"
            await cl.Message(
                content=(
                    f"Unknown package `{target_raw}`.\n"
                    f"Available packages: {available}"
                )
            ).send()
            return True
        cl.user_session.set(SESSION_PACKAGE_ID_KEY, resolved)
        cl.user_session.set(SESSION_PACKAGE_SOURCE_KEY, PACKAGE_SOURCE_MANUAL)
        await cl.Message(
            content=f"Pinned package `{resolved}` for this chat session."
        ).send()
        return True

    await cl.Message(content="Unknown `/package` command. Use `/package help`.").send()
    return True


def _normalize_status_label(value: str | None) -> str | None:
    """Normalize streaming status text for UI display.

    Parameters
    ----------
    value : str or None
        Raw status label.

    Returns
    -------
    str or None
        Trimmed label or `None` when empty.
    """

    if not value:
        return None
    cleaned = str(value).strip()
    return cleaned or None


async def _maybe_update_status(
    status_msg: cl.Message | None, status_label: str | None, last_status: str | None
) -> tuple[cl.Message | None, str | None]:
    """Create or update an ephemeral status message if label changed.

    Parameters
    ----------
    status_msg : cl.Message or None
        Existing status message handle.
    status_label : str or None
        Candidate status text.
    last_status : str or None
        Previously rendered status label.

    Returns
    -------
    tuple
        Updated `(status_msg, current_label)` pair.
    """

    label = _normalize_status_label(status_label)
    if not label or label == last_status:
        return status_msg, last_status
    if status_msg is None:
        status_msg = cl.Message(
            content=label,
            author="status",
            type="system_message",
        )
        await status_msg.send()
    else:
        status_msg.content = label
        await status_msg.update()
    return status_msg, label


def _normalize_subdir(value: str) -> str:
    """Normalize a relative storage subdirectory string.

    Parameters
    ----------
    value : str
        Raw subdirectory value.

    Returns
    -------
    str
        Slash-normalized, dot-segment-free relative path.
    """

    cleaned = (value or "").replace("\\", "/")
    parts = [part for part in cleaned.split("/") if part and part != "."]
    return "/".join(parts)


def _join_url(root_path: str, path: str) -> str:
    """Join a root URL path prefix with a child path.

    Parameters
    ----------
    root_path : str
        Optional base path prefix.
    path : str
        Child path to append.

    Returns
    -------
    str
        Joined URL path with exactly one slash at the boundary.
    """

    root = (root_path or "").rstrip("/")
    tail = "/" + path.lstrip("/")
    return f"{root}{tail}" if root else tail


class LocalPublicStorageClient(BaseStorageClient):
    """Chainlit storage client that persists artifacts under `/public`."""

    def __init__(self, public_root: Path, subdir: str, root_path: str):
        """Initialize local storage paths and URL prefix.

        Parameters
        ----------
        public_root : Path
            Root filesystem path for Chainlit static public assets.
        subdir : str
            Relative subdirectory used to store uploaded objects.
        root_path : str
            Chainlit application root path prefix.
        """

        self.public_root = public_root
        self.subdir = _normalize_subdir(subdir) or ".chainlit/artifacts"
        self.base_dir = (self.public_root / self.subdir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.public_url_prefix = _join_url(root_path, "/public")

    def _resolve_path(self, object_key: str) -> Path:
        """Resolve and validate an artifact key under the storage base directory.

        Parameters
        ----------
        object_key : str
            Relative object key provided by Chainlit.

        Returns
        -------
        Path
            Resolved filesystem path inside the storage base directory.

        Raises
        ------
        ValueError
            Raised when the resolved path escapes the base directory.
        """

        key = str(object_key or "").lstrip("/").replace("\\", "/")
        path = (self.base_dir / key).resolve()
        try:
            path.relative_to(self.base_dir)
        except ValueError as exc:
            raise ValueError("Invalid object key") from exc
        return path

    def _url_for_key(self, object_key: str) -> str:
        """Build the public URL for a stored object key.

        Parameters
        ----------
        object_key : str
            Relative object key.

        Returns
        -------
        str
            Public URL pointing to the stored file.
        """

        key = str(object_key or "").lstrip("/").replace("\\", "/")
        rel = f"{self.subdir}/{key}" if self.subdir else key
        return f"{self.public_url_prefix}/{rel}"

    async def upload_file(
        self,
        object_key: str,
        data: bytes | str,
        mime: str = "application/octet-stream",
        overwrite: bool = True,
        content_disposition: str | None = None,
    ) -> dict:
        """Store a file-like payload to local public storage.

        Parameters
        ----------
        object_key : str
            Relative key used for local persistence and URL generation.
        data : bytes or str
            Payload content to write.
        mime : str, optional
            MIME type from the caller (unused by local implementation).
        overwrite : bool, optional
            Whether existing files can be replaced.
        content_disposition : str or None, optional
            Content disposition hint (unused by local implementation).

        Returns
        -------
        dict
            Mapping containing `object_key` and generated `url`.
        """

        _ = mime
        _ = content_disposition
        path = self._resolve_path(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not overwrite and path.exists():
            return {"object_key": object_key, "url": self._url_for_key(object_key)}
        if isinstance(data, str):
            data = data.encode("utf-8")
        async with aiofiles.open(path, "wb") as handle:
            await handle.write(data)
        return {"object_key": object_key, "url": self._url_for_key(object_key)}

    async def delete_file(self, object_key: str) -> bool:
        """Delete a stored object key if present.

        Parameters
        ----------
        object_key : str
            Relative object key to remove.

        Returns
        -------
        bool
            Always `True`, including when the file is already missing.
        """

        path = self._resolve_path(object_key)
        try:
            path.unlink()
        except FileNotFoundError:
            return True
        return True

    async def get_read_url(self, object_key: str) -> str:
        """Return the public URL for a stored object key.

        Parameters
        ----------
        object_key : str
            Relative object key.

        Returns
        -------
        str
            Public URL for read access.
        """

        return self._url_for_key(object_key)

    async def close(self) -> None:
        """Close the storage client.

        Returns
        -------
        None
            No-op for local filesystem-backed storage.
        """

        return None


def _resolve_public_root() -> Path:
    """Resolve effective Chainlit public root and seed packaged assets if needed."""

    configured = Path(public_dir).expanduser()
    if not configured.is_absolute():
        configured = (APP_ROOT / configured).resolve()
    package_public = Path(__file__).resolve().parents[1] / "public"

    try:
        configured.mkdir(parents=True, exist_ok=True)
    except OSError:
        if package_public.is_dir():
            return package_public
        return configured

    if package_public.is_dir():
        for asset in package_public.iterdir():
            target = configured / asset.name
            if target.exists():
                continue
            if asset.is_file():
                try:
                    target.write_bytes(asset.read_bytes())
                except OSError:
                    continue
    return configured


def _build_storage_provider() -> BaseStorageClient:
    """Instantiate the local public storage provider for Chainlit.

    Returns
    -------
    BaseStorageClient
        Local storage implementation backed by the public directory.
    """

    subdir = os.getenv("CHAINLIT_LOCAL_STORAGE_SUBDIR", ".chainlit/artifacts")
    return LocalPublicStorageClient(
        public_root=_resolve_public_root(),
        subdir=subdir,
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
    """Extract sqlite database path from a SQLAlchemy connection URL.

    Parameters
    ----------
    url : str
        SQLAlchemy connection string.

    Returns
    -------
    Path or None
        SQLite path when URL points to sqlite, otherwise `None`.
    """

    try:
        parsed = make_url(url)
    except Exception:
        return None
    if not parsed.drivername.startswith("sqlite"):
        return None
    if not parsed.database:
        return None
    path = Path(parsed.database)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _ensure_sqlite_schema(db_path: Path, schema_sql: str) -> None:
    """Ensure sqlite database schema exists by executing DDL script.

    Parameters
    ----------
    db_path : Path
        SQLite database file path.
    schema_sql : str
        SQL script containing `CREATE TABLE IF NOT EXISTS` statements.

    Returns
    -------
    None
        Schema is applied in place.
    """

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(schema_sql)


def _ensure_sqlite_columns(db_path: Path, table: str, columns: dict[str, str]) -> None:
    """Backfill missing sqlite columns for an existing table.

    Parameters
    ----------
    db_path : Path
        SQLite database path.
    table : str
        Table name to inspect.
    columns : dict of str to str
        Mapping of column names to SQL types to add when absent.

    Returns
    -------
    None
        Table is altered in place when required.
    """

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        existing = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table});").fetchall()
        }
        for name, col_type in columns.items():
            if name in existing:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type};")
        conn.commit()


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
    """Normalize login usernames for stable auth lookups.

    Parameters
    ----------
    username : str
        Raw username input.

    Returns
    -------
    str
        Trimmed lowercase username.
    """

    return username.strip().lower()


def _validate_password(password: str) -> bool:
    """Validate password length against configured minimum.

    Parameters
    ----------
    password : str
        Password candidate.

    Returns
    -------
    bool
        `True` when length meets policy.
    """

    return len(password) >= AUTH_MIN_PASSWORD_LEN


def _validate_email(email: str) -> bool:
    """Validate login/signup identifier as an email-like string.

    Parameters
    ----------
    email : str
        Email candidate.

    Returns
    -------
    bool
        `True` when the input matches a basic email pattern.
    """

    return bool(EMAIL_PATTERN.fullmatch(email))


def _count_auth_users() -> int:
    """Count registered users in the auth database.

    Returns
    -------
    int
        Total number of rows in `auth_users`.
    """

    with _open_auth_db() as conn:
        row = conn.execute("SELECT COUNT(*) AS user_count FROM auth_users").fetchone()
    if row is None:
        return 0
    return int(row["user_count"] or 0)


def _signup_status_payload() -> dict[str, Any]:
    """Build current self-signup policy payload for API/UI.

    Returns
    -------
    dict[str, Any]
        Signup availability metadata.
    """

    user_count = _count_auth_users()
    max_users = AUTH_MAX_USERS if AUTH_MAX_USERS > 0 else None
    enabled = bool(AUTH_SIGNUP_ENABLED)
    reason: str | None = None
    message = "Self sign-up is available."

    if not AUTH_SIGNUP_ENABLED:
        enabled = False
        reason = "disabled_by_admin"
        message = "Sign up is disabled by admin."
    elif AUTH_MAX_USERS > 0 and user_count >= AUTH_MAX_USERS:
        enabled = False
        reason = "user_limit_reached"
        message = f"Sign up is closed. User limit reached ({AUTH_MAX_USERS})."

    return {
        "enabled": enabled,
        "reason": reason,
        "message": message,
        "user_count": user_count,
        "max_users": max_users,
        "min_password_length": AUTH_MIN_PASSWORD_LEN,
    }


def _normalize_group(group_name: str | None) -> str:
    """Normalize prompt-quota group name with default fallback.

    Parameters
    ----------
    group_name : str or None
        Raw group value from user metadata.

    Returns
    -------
    str
        Normalized group name or default group.
    """

    if not group_name:
        return DEFAULT_PROMPT_GROUP
    normalized = str(group_name).strip().lower()
    return normalized or DEFAULT_PROMPT_GROUP


def _get_prompt_timezone() -> timezone:
    """Resolve timezone used for daily prompt-limit windows.

    Returns
    -------
    datetime.timezone
        Configured timezone or UTC fallback when invalid.
    """

    if PROMPT_DAY_TZ.upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(PROMPT_DAY_TZ)
    except Exception:
        LOGGER.warning("Invalid PROMPT_DAY_TZ=%r, defaulting to UTC", PROMPT_DAY_TZ)
        return timezone.utc


def _current_usage_day(now: datetime | None = None) -> tuple[str, datetime]:
    """Compute current usage-day key in configured quota timezone.

    Parameters
    ----------
    now : datetime or None, optional
        Reference datetime. Uses current time when omitted.

    Returns
    -------
    tuple
        `(day_iso, localized_now)` for quota accounting.
    """

    tz = _get_prompt_timezone()
    now = now.astimezone(tz) if now else datetime.now(tz)
    return now.date().isoformat(), now


def _next_usage_reset(now: datetime | None = None) -> datetime:
    """Compute next quota reset timestamp (next local midnight).

    Parameters
    ----------
    now : datetime or None, optional
        Reference datetime. Uses current time when omitted.

    Returns
    -------
    datetime
        Timestamp for the next quota reset.
    """

    _, current = _current_usage_day(now)
    next_midnight = (current + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return next_midnight


def _get_prompt_limit(group_name: str) -> int:
    """Return the daily prompt limit for a user group.

    Parameters
    ----------
    group_name : str
        User quota group name.

    Returns
    -------
    int
        Configured per-day prompt limit.
    """

    normalized = _normalize_group(group_name)
    limit = PROMPT_LIMITS.get(normalized)
    if limit is None:
        limit = PROMPT_LIMITS.get(DEFAULT_PROMPT_GROUP, 0)
    return limit


def _get_user_group(username: str) -> str:
    """Lookup a user's quota group from the auth database.

    Parameters
    ----------
    username : str
        Username identifier.

    Returns
    -------
    str
        Normalized group name, defaulting when user/group is missing.
    """

    normalized = _normalize_username(username)
    if not normalized:
        return DEFAULT_PROMPT_GROUP
    with _open_auth_db() as conn:
        row = conn.execute(
            "SELECT group_name FROM auth_users WHERE username = ?",
            (normalized,),
        ).fetchone()
    if row is None:
        return DEFAULT_PROMPT_GROUP
    return _normalize_group(row["group_name"])


def _get_current_user_identifier() -> str | None:
    """Extract current Chainlit user identifier from session context.

    Returns
    -------
    str or None
        User identifier when authenticated, otherwise `None`.
    """

    user = cl.user_session.get("user")
    if not user:
        return None
    if isinstance(user, dict):
        identifier = user.get("identifier")
        return identifier if isinstance(identifier, str) and identifier else None
    identifier = getattr(user, "identifier", None)
    return identifier if isinstance(identifier, str) and identifier else None


def _get_current_chainlit_session_identifier() -> str | None:
    """Extract current Chainlit websocket session identifier when available.

    Returns
    -------
    str or None
        Session identifier for anonymous fallback scoping.
    """

    try:
        session = chainlit_context.session
    except Exception:
        return None

    candidate = getattr(session, "id", None)
    if isinstance(candidate, str) and candidate:
        return candidate

    candidate = getattr(session, "session_id", None)
    if isinstance(candidate, str) and candidate:
        return candidate
    return None


def _get_current_chainlit_session_object() -> Any | None:
    """Return current Chainlit session object when available."""

    try:
        return chainlit_context.session
    except Exception:
        return None


def _resolve_activity_owner_keys(user_identifier: str | None = None) -> list[str]:
    """Resolve owner keys used for in-memory active thread tracking.

    Parameters
    ----------
    user_identifier : str or None, optional
        Pre-resolved authenticated identifier.

    Returns
    -------
    list[str]
        One or more stable keys for authenticated and/or anonymous scopes.
    """

    keys: list[str] = []
    cleaned_user = None
    if isinstance(user_identifier, str) and user_identifier.strip():
        cleaned_user = user_identifier.strip().lower()
    elif isinstance(user_identifier, str):
        cleaned_user = None
    else:
        current_user = _get_current_user_identifier()
        if isinstance(current_user, str) and current_user.strip():
            cleaned_user = current_user.strip().lower()
        else:
            session = _get_current_chainlit_session_object()
            if session is not None:
                session_user = getattr(session, "user", None)
                if isinstance(session_user, dict):
                    identifier = session_user.get("identifier")
                else:
                    identifier = getattr(session_user, "identifier", None)
                if isinstance(identifier, str) and identifier.strip():
                    cleaned_user = identifier.strip().lower()

    if cleaned_user:
        keys.append(f"user:{cleaned_user}")

    session_identifier = _get_current_chainlit_session_identifier()
    if isinstance(session_identifier, str) and session_identifier:
        keys.append(f"session:{session_identifier}")
    return keys


def _owner_scope_matches(
    binding_owner_keys: set[str], candidate_owner_keys: set[str]
) -> bool:
    """Check whether an active-run binding belongs to current requester scope.

    For authenticated users, require an explicit key overlap.
    For anonymous sessions (no `user:` keys on either side), allow thread-local
    matching even when websocket session ids rotate on refresh.
    """

    if binding_owner_keys & candidate_owner_keys:
        return True
    binding_has_user = any(key.startswith("user:") for key in binding_owner_keys)
    candidate_has_user = any(key.startswith("user:") for key in candidate_owner_keys)
    if not binding_has_user and not candidate_has_user:
        return True
    return False


async def _thread_has_active_run_for_owner(
    thread_id: str | None, owner_keys: list[str]
) -> bool:
    """Return whether one thread currently has an in-flight run for this owner."""

    if not thread_id:
        return False
    candidate_keys = {key for key in owner_keys if key}

    async with _ACTIVE_THREADS_LOCK:
        binding = _ACTIVE_RUNS_BY_THREAD.get(thread_id)
        if binding is None:
            return False
        run_task = binding.run_task
        if run_task is None or run_task.done():
            _ACTIVE_RUNS_BY_THREAD.pop(thread_id, None)
            return False
        return _owner_scope_matches(binding.owner_keys, candidate_keys)


async def _cancel_active_run_for_thread(
    thread_id: str | None, owner_keys: list[str]
) -> bool:
    """Cancel one in-flight run for the current owner when present."""

    if not thread_id:
        return False
    candidate_keys = {key for key in owner_keys if key}

    run_task: asyncio.Task[Any] | None = None
    async with _ACTIVE_THREADS_LOCK:
        binding = _ACTIVE_RUNS_BY_THREAD.get(thread_id)
        if binding is None:
            return False
        if not _owner_scope_matches(binding.owner_keys, candidate_keys):
            return False
        run_task = binding.run_task
        if run_task is None or run_task.done():
            _ACTIVE_RUNS_BY_THREAD.pop(thread_id, None)
            return False

    run_task.cancel()
    return True


async def _mark_thread_running(owner_keys: list[str], thread_id: str | None) -> None:
    """Record a thread as actively running for one or more owners."""

    if not thread_id:
        return
    unique_keys = [key for key in dict.fromkeys(owner_keys) if key]
    if not unique_keys:
        return

    async with _ACTIVE_THREADS_LOCK:
        for owner_key in unique_keys:
            active = _ACTIVE_THREADS_BY_OWNER.setdefault(owner_key, set())
            active.add(thread_id)


async def _mark_thread_stopped(owner_keys: list[str], thread_id: str | None) -> None:
    """Remove one thread from active tracking for one or more owners."""

    if not thread_id:
        return
    unique_keys = [key for key in dict.fromkeys(owner_keys) if key]
    if not unique_keys:
        return

    async with _ACTIVE_THREADS_LOCK:
        for owner_key in unique_keys:
            active = _ACTIVE_THREADS_BY_OWNER.get(owner_key)
            if not active:
                continue
            active.discard(thread_id)
            if not active:
                _ACTIVE_THREADS_BY_OWNER.pop(owner_key, None)


async def _get_active_threads_for_owner_keys(owner_keys: list[str]) -> list[str]:
    """Return sorted active thread ids for current owner key set."""

    unique_keys = [key for key in dict.fromkeys(owner_keys) if key]
    if not unique_keys:
        return []

    async with _ACTIVE_THREADS_LOCK:
        active: set[str] = set()
        for owner_key in unique_keys:
            active.update(_ACTIVE_THREADS_BY_OWNER.get(owner_key, set()))
        return sorted(active)


async def _register_active_run(
    thread_id: str | None,
    owner_keys: list[str],
    stream_session: Any | None,
    run_task: asyncio.Task[Any] | None,
) -> None:
    """Register one active run binding for reconnect-aware streaming."""

    if not thread_id:
        return
    unique_keys = {key for key in owner_keys if key}
    async with _ACTIVE_THREADS_LOCK:
        _ACTIVE_RUNS_BY_THREAD[thread_id] = _ActiveRunBinding(
            owner_keys=unique_keys,
            stream_session=stream_session,
            run_task=run_task,
        )


async def _unregister_active_run(thread_id: str | None) -> None:
    """Remove one active run binding after completion."""

    if not thread_id:
        return
    async with _ACTIVE_THREADS_LOCK:
        _ACTIVE_RUNS_BY_THREAD.pop(thread_id, None)


async def _rebind_active_run_session(thread_id: str | None, owner_keys: list[str]) -> None:
    """Rebind one active run's emitter session to the current websocket session."""

    if not thread_id:
        return
    candidate_keys = {key for key in owner_keys if key}
    current_session = _get_current_chainlit_session_object()
    if current_session is None:
        return

    async with _ACTIVE_THREADS_LOCK:
        binding = _ACTIVE_RUNS_BY_THREAD.get(thread_id)
        if binding is None:
            return
        if not _owner_scope_matches(binding.owner_keys, candidate_keys):
            return
        source = binding.stream_session
        if source is None:
            return
        try:
            source.emit = current_session.emit
            source.emit_call = current_session.emit_call
            source.environ = current_session.environ
            # Keep stop/cancel targeting the original in-flight task after refresh.
            if binding.run_task is not None and not binding.run_task.done():
                current_session.current_task = binding.run_task
        except Exception as exc:
            LOGGER.debug("Failed to rebind active run session for %s: %s", thread_id, exc)


async def _sync_running_threads_window_state() -> None:
    """Re-send running-thread start signals after client reconnect/refresh."""

    owner_keys = _resolve_activity_owner_keys()
    active_threads = await _get_active_threads_for_owner_keys(owner_keys)
    current_thread_id = _get_current_thread_id()

    # Owner-key matching can miss runs after refresh because websocket session ids
    # rotate. If the currently viewed thread still has an active run binding, keep
    # it in sync explicitly.
    if current_thread_id and current_thread_id not in active_threads:
        if await _thread_has_active_run_for_owner(current_thread_id, owner_keys):
            active_threads = [current_thread_id, *active_threads]
    active_threads = list(dict.fromkeys(active_threads))
    if active_threads:
        current_session = _get_current_chainlit_session_object()
        if current_session is not None:
            try:
                # Restore Chainlit's loading state so the native stop button reappears
                # after refresh while a run is still active.
                await current_session.emit("task_start", {})
                # Stop visibility also depends on first-interaction state in Chainlit.
                # Re-emit for the current thread to recover this state on refresh.
                if current_thread_id:
                    await current_session.emit(
                        "first_interaction",
                        {"interaction": "resume", "thread_id": current_thread_id},
                    )
            except Exception as exc:
                LOGGER.debug(
                    "Failed to restore reconnect task state for active runs: %s", exc
                )
    for thread_id in active_threads:
        await _rebind_active_run_session(thread_id, owner_keys)
        await cl.send_window_message(
            {
                "type": "assistant_thinking",
                "status": "start",
                "thread_id": thread_id,
            }
        )


def _consume_prompt_quota(username: str) -> tuple[bool, int, int, str, datetime]:
    """Consume one prompt quota unit for a user when allowed.

    Parameters
    ----------
    username : str
        Authenticated username.

    Returns
    -------
    tuple
        `(allowed, used_count, limit, group_name, reset_at)`.
    """

    normalized = _normalize_username(username)
    if not normalized:
        return True, 0, 0, DEFAULT_PROMPT_GROUP, _next_usage_reset()

    group_name = _get_user_group(normalized)
    limit = _get_prompt_limit(group_name)
    if limit <= 0:
        return True, 0, limit, group_name, _next_usage_reset()

    usage_day, now = _current_usage_day()
    reset_at = _next_usage_reset(now)
    now_iso = now.isoformat()

    with _open_auth_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT count FROM auth_usage_daily WHERE username = ? AND day = ?",
            (normalized, usage_day),
        ).fetchone()
        current = int(row["count"]) if row else 0
        if current >= limit:
            conn.rollback()
            return False, current, limit, group_name, reset_at

        new_count = current + 1
        if row:
            conn.execute(
                "UPDATE auth_usage_daily SET count = ?, updated_at = ? "
                "WHERE username = ? AND day = ?",
                (new_count, now_iso, normalized, usage_day),
            )
        else:
            conn.execute(
                "INSERT INTO auth_usage_daily (username, day, count, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (normalized, usage_day, new_count, now_iso),
            )
        conn.commit()

    return True, new_count, limit, group_name, reset_at


def _open_auth_db() -> sqlite3.Connection:
    """Open the auth sqlite database with row and FK settings enabled.

    Returns
    -------
    sqlite3.Connection
        Configured sqlite connection.
    """

    conn = sqlite3.connect(AUTH_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _get_user(username: str) -> sqlite3.Row | None:
    """Fetch an auth user row by username.

    Parameters
    ----------
    username : str
        Username identifier.

    Returns
    -------
    sqlite3.Row or None
        User record when present, otherwise `None`.
    """

    normalized = _normalize_username(username)
    if not normalized:
        return None
    with _open_auth_db() as conn:
        row = conn.execute(
            "SELECT username, password_hash, created_at, last_login "
            "FROM auth_users WHERE username = ?",
            (normalized,),
        ).fetchone()
    return row


def _create_user(username: str, password: str) -> sqlite3.Row | None:
    """Create a new auth user account with hashed password.

    Parameters
    ----------
    username : str
        Desired username.
    password : str
        Plain-text password to hash and store.

    Returns
    -------
    sqlite3.Row or None
        Created user row when successful, otherwise `None`.
    """

    normalized = _normalize_username(username)
    if not normalized:
        return None
    if not _validate_password(password):
        return None
    now = datetime.now(timezone.utc).isoformat()
    password_hash = pbkdf2_sha256.hash(password)
    try:
        with _open_auth_db() as conn:
            conn.execute(
                "INSERT INTO auth_users (username, password_hash, created_at, group_name) "
                "VALUES (?, ?, ?, ?)",
                (normalized, password_hash, now, DEFAULT_PROMPT_GROUP),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        return None
    return _get_user(normalized)


def _register_signup_user(username: str, password: str) -> tuple[sqlite3.Row | None, str]:
    """Register a user while enforcing self-signup policy atomically.

    Parameters
    ----------
    username : str
        Email-like username.
    password : str
        Plain-text password.

    Returns
    -------
    tuple[sqlite3.Row or None, str]
        `(user_row, reason)` where reason is one of:
        `created`, `invalid_username`, `invalid_password`, `disabled_by_admin`,
        `user_limit_reached`, `already_exists`, `db_error`.
    """

    normalized = _normalize_username(username)
    if not normalized or not _validate_email(normalized):
        return None, "invalid_username"
    if not _validate_password(password):
        return None, "invalid_password"

    now = datetime.now(timezone.utc).isoformat()
    try:
        with _open_auth_db() as conn:
            conn.execute("BEGIN IMMEDIATE")

            if not AUTH_SIGNUP_ENABLED:
                conn.rollback()
                return None, "disabled_by_admin"

            if AUTH_MAX_USERS > 0:
                row = conn.execute(
                    "SELECT COUNT(*) AS user_count FROM auth_users"
                ).fetchone()
                count = int(row["user_count"] or 0) if row else 0
                if count >= AUTH_MAX_USERS:
                    conn.rollback()
                    return None, "user_limit_reached"

            existing = conn.execute(
                "SELECT 1 FROM auth_users WHERE username = ?",
                (normalized,),
            ).fetchone()
            if existing is not None:
                conn.rollback()
                return None, "already_exists"

            password_hash = pbkdf2_sha256.hash(password)
            conn.execute(
                "INSERT INTO auth_users (username, password_hash, created_at, group_name) "
                "VALUES (?, ?, ?, ?)",
                (normalized, password_hash, now, DEFAULT_PROMPT_GROUP),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        return None, "already_exists"
    except sqlite3.Error:
        LOGGER.exception("Failed to register signup user %s", normalized)
        return None, "db_error"

    created = _get_user(normalized)
    if created is None:
        return None, "db_error"
    return created, "created"


def _update_last_login(username: str) -> None:
    """Update the `last_login` timestamp for a user.

    Parameters
    ----------
    username : str
        Normalized username.

    Returns
    -------
    None
        User row is updated in place.
    """

    now = datetime.now(timezone.utc).isoformat()
    with _open_auth_db() as conn:
        conn.execute(
            "UPDATE auth_users SET last_login = ? WHERE username = ?",
            (now, username),
        )
        conn.commit()


def _authenticate_user(username: str, password: str) -> str | None:
    """Authenticate a user and optionally auto-register unknown users.

    Parameters
    ----------
    username : str
        Username input.
    password : str
        Password input.

    Returns
    -------
    str or None
        Authenticated identifier when credentials are valid, else `None`.
    """

    normalized = _normalize_username(username)
    if not normalized or not password:
        return None
    user_row = _get_user(normalized)
    if user_row is None:
        if not AUTH_AUTO_REGISTER:
            return None
        created = _create_user(normalized, password)
        if created is None:
            return None
        _update_last_login(normalized)
        return normalized

    if not pbkdf2_sha256.verify(password, user_row["password_hash"]):
        return None

    _update_last_login(normalized)
    return normalized


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
    """Extract best-effort text content from heterogeneous stream payloads.

    Parameters
    ----------
    payload : dict
        Event payload emitted by Codex streaming output.

    Returns
    -------
    str or None
        First non-empty text fragment found in known fields.
    """

    def from_obj(obj: object) -> str | None:
        """Extract text from one object-shaped payload node.

        Parameters
        ----------
        obj : object
            Candidate mapping-like payload fragment.

        Returns
        -------
        str or None
            Text fragment or `None` when unavailable.
        """

        if not isinstance(obj, dict):
            return None
        for key in ("text", "content", "message", "raw_content", "summary_text"):
            value = obj.get(key)
            if isinstance(value, str) and value:
                return value
        content = obj.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for entry in content:
                if isinstance(entry, str):
                    parts.append(entry)
                elif isinstance(entry, dict):
                    for key in ("text", "content", "message", "raw_content"):
                        value = entry.get(key)
                        if isinstance(value, str) and value:
                            parts.append(value)
                            break
            if parts:
                return "".join(parts)
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


def _extract_command(payload: dict) -> str | None:
    """Extract command text from a stream payload.

    Parameters
    ----------
    payload : dict
        Event payload emitted during command/tool execution.

    Returns
    -------
    str or None
        Command string when present, otherwise `None`.
    """

    for key in ("command", "cmd", "parsed_cmd", "shell_command", "action", "text"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _truncate_history_entry(text: str) -> str:
    """Truncate one history entry to configured maximum length.

    Parameters
    ----------
    text : str
        History message text.

    Returns
    -------
    str
        Original or truncated entry with overflow note.
    """

    if HISTORY_ENTRY_MAX_CHARS <= 0:
        return ""
    if len(text) <= HISTORY_ENTRY_MAX_CHARS:
        return text
    overflow = len(text) - HISTORY_ENTRY_MAX_CHARS
    return f"{text[:HISTORY_ENTRY_MAX_CHARS]}... ({overflow} chars truncated)"


def _append_history(
    history: list[tuple[str, str]], role: str, content: str
) -> list[tuple[str, str]]:
    """Append one chat turn to bounded session history.

    Parameters
    ----------
    history : list of tuple[str, str]
        Existing `(role, content)` pairs.
    role : str
        Message role label.
    content : str
        Message content.

    Returns
    -------
    list of tuple[str, str]
        Updated history honoring entry, message-count, and total-size limits.
    """

    content = (content or "").strip()
    if not content:
        return history
    content = _truncate_history_entry(content)
    history.append((role, content))
    if HISTORY_MAX_MESSAGES > 0 and len(history) > HISTORY_MAX_MESSAGES:
        history = history[-HISTORY_MAX_MESSAGES:]
    if HISTORY_MAX_CHARS > 0:
        total = sum(len(item[1]) for item in history)
        while history and total > HISTORY_MAX_CHARS:
            dropped = history.pop(0)
            total -= len(dropped[1])
    return history


def _format_history(history: list[tuple[str, str]]) -> str:
    """Render chat history into the prompt transcript format.

    Parameters
    ----------
    history : list of tuple[str, str]
        `(role, content)` history pairs.

    Returns
    -------
    str
        Newline-delimited transcript for runner prompts.
    """

    lines: list[str] = []
    for role, content in history:
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {content}")
    return "\n".join(lines)


def _build_prompt(history: list[tuple[str, str]], user_text: str) -> str:
    """Build a size-limited prompt transcript including current user text.

    Parameters
    ----------
    history : list of tuple[str, str]
        Existing session history.
    user_text : str
        Latest user message content.

    Returns
    -------
    str
        Prompt transcript bounded by `MAX_PROMPT_CHARS`.
    """

    temp = history + [("user", (user_text or "").strip())]
    if not temp:
        return user_text
    if MAX_PROMPT_CHARS <= 0:
        return _format_history(temp)
    start = 0
    while start < len(temp):
        candidate = _format_history(temp[start:])
        if len(candidate) <= MAX_PROMPT_CHARS:
            return candidate
        start += 1
    return _format_history([temp[-1]])


def _resolve_workspaces_root() -> Path:
    """Resolve workspace root path used for artifact attachment.

    Returns
    -------
    Path
        Existing configured workspace root or local fallback.
    """

    try:
        return resolve_default_workspaces_root()
    except OSError:
        fallback = Path.cwd() / "workspaces"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _extract_candidate_paths(text: str) -> list[str]:
    """Extract likely file-path tokens from assistant output text.

    Parameters
    ----------
    text : str
        Assistant output text.

    Returns
    -------
    list of str
        Sorted unique file-like path candidates.
    """

    if not text:
        return []
    candidates: set[str] = set()
    for raw in re.split(r"\s+", text):
        token = raw.strip("`'\".,;:()[]{}<>")
        if not token:
            continue
        if "://" in token:
            continue
        if "/" not in token:
            continue
        if token.endswith("/"):
            continue
        if "." not in Path(token).name:
            continue
        candidates.add(token)
    return sorted(candidates)


def _resolve_artifact_path(repo_root: Path, token: str) -> tuple[Path, Path] | None:
    """Resolve a candidate artifact token to a real file under repo root.

    Parameters
    ----------
    repo_root : Path
        Workspace repository root.
    token : str
        Candidate path token extracted from text.

    Returns
    -------
    tuple[Path, Path] or None
        `(absolute_path, repo_relative_path)` when valid, else `None`.
    """

    candidates = [token]
    if "/repo/" in token:
        candidates.append(token.split("/repo/", 1)[1])
    for candidate in candidates:
        path = Path(candidate)
        if not path.is_absolute():
            path = repo_root / path
        path = path.resolve(strict=False)
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(repo_root)
        except ValueError:
            continue
        if ARTIFACT_PREFIXES:
            if not relative.parts or relative.parts[0] not in ARTIFACT_PREFIXES:
                continue
        return path, relative
    return None


def _element_for_path(path: Path, relative: Path):
    """Create a Chainlit element object for a file path.

    Parameters
    ----------
    path : Path
        Absolute artifact path.
    relative : Path
        Repo-relative artifact path for display name.

    Returns
    -------
    cl.Element
        `Image`, `Pdf`, or generic `File` element for attachment.
    """

    name = relative.as_posix()
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return cl.Image(name=name, path=str(path), display="inline", size="large")
    if ext == ".pdf":
        return cl.Pdf(name=name, path=str(path))
    return cl.File(name=name, path=str(path))


def _snapshot_repo(repo_root: Path) -> dict[str, tuple[int, int]]:
    """Snapshot repository files for change detection.

    Parameters
    ----------
    repo_root : Path
        Workspace repository root.

    Returns
    -------
    dict[str, tuple[int, int]]
        Mapping of relative path to `(mtime_ns, size)` metadata.
    """

    snapshot: dict[str, tuple[int, int]] = {}
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_SNAPSHOT_DIRS]
        for filename in files:
            path = Path(root) / filename
            try:
                stat = path.stat()
            except OSError:
                continue
            try:
                rel = path.relative_to(repo_root).as_posix()
            except ValueError:
                continue
            snapshot[rel] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def _diff_snapshots(
    before: dict[str, tuple[int, int]],
    after: dict[str, tuple[int, int]],
) -> tuple[list[str], list[str]]:
    """Compute created and modified files between two snapshots.

    Parameters
    ----------
    before : dict[str, tuple[int, int]]
        Baseline snapshot.
    after : dict[str, tuple[int, int]]
        Snapshot after execution.

    Returns
    -------
    tuple[list[str], list[str]]
        Sorted `(created_files, modified_files)` path lists.
    """

    created = sorted(path for path in after.keys() if path not in before)
    modified = sorted(
        path for path, meta in after.items() if path in before and before[path] != meta
    )
    return created, modified


def _truncate_items(items: list[str], max_items: int) -> list[str]:
    """Truncate a list to a maximum count with overflow marker.

    Parameters
    ----------
    items : list of str
        Input entries.
    max_items : int
        Maximum number of entries to keep.

    Returns
    -------
    list of str
        Possibly truncated list including an overflow summary row.
    """

    if max_items <= 0:
        return []
    if len(items) <= max_items:
        return items
    return items[:max_items] + [f"... ({len(items) - max_items} more)"]


def _dedupe_preserve(items: list[str]) -> list[str]:
    """Remove duplicates while preserving original order.

    Parameters
    ----------
    items : list of str
        Input sequence.

    Returns
    -------
    list of str
        De-duplicated sequence preserving first occurrence order.
    """

    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _truncate_entry(text: str) -> str:
    """Truncate one transparency report entry by character budget.

    Parameters
    ----------
    text : str
        Entry text.

    Returns
    -------
    str
        Original or truncated entry with overflow marker.
    """

    if TRANSPARENCY_MAX_ENTRY_CHARS <= 0:
        return text
    if len(text) <= TRANSPARENCY_MAX_ENTRY_CHARS:
        return text
    overflow = len(text) - TRANSPARENCY_MAX_ENTRY_CHARS
    return f"{text[:TRANSPARENCY_MAX_ENTRY_CHARS]}... ({overflow} more chars)"


def _format_transparency_report(
    active_package: str | None,
    tool_calls: list[str],
    commands_run: list[str],
    created_files: list[str],
    modified_files: list[str],
    log_entries: list[str],
    error_entries: list[str],
) -> str:
    """Build a structured transparency report for post-run disclosure.

    Parameters
    ----------
    active_package : str or None
        Selected package id used for this run.
    tool_calls : list of str
        Observed tool call summaries.
    commands_run : list of str
        Executed shell command summaries.
    created_files : list of str
        Files created during run.
    modified_files : list of str
        Files modified during run.
    log_entries : list of str
        Captured runner log lines.
    error_entries : list of str
        Captured error messages.

    Returns
    -------
    str
        Markdown text report.
    """

    lines: list[str] = ["**Transparency**"]
    lines.append(
        f"Active package: `{active_package}`"
        if active_package
        else "Active package: none"
    )

    lines.append("Tool calls:")
    for entry in _truncate_items(tool_calls, TRANSPARENCY_MAX_ITEMS) or ["none"]:
        lines.append(f"- {_truncate_entry(entry)}")

    lines.append("Commands run:")
    for entry in _truncate_items(commands_run, TRANSPARENCY_MAX_ITEMS) or ["none"]:
        lines.append(f"- {_truncate_entry(entry)}")

    lines.append("Files created:")
    for entry in _truncate_items(created_files, TRANSPARENCY_MAX_ITEMS) or ["none"]:
        lines.append(f"- {_truncate_entry(entry)}")

    lines.append("Files modified:")
    for entry in _truncate_items(modified_files, TRANSPARENCY_MAX_ITEMS) or ["none"]:
        lines.append(f"- {_truncate_entry(entry)}")

    lines.append("Errors/logs:")
    combined = []
    combined.extend([f"[error] {e}" for e in error_entries])
    combined.extend([f"[log] {l}" for l in log_entries])
    combined = _truncate_items(combined, TRANSPARENCY_MAX_LOG_ENTRIES)
    for entry in combined or ["none"]:
        lines.append(f"- {_truncate_entry(entry)}")

    summary = (
        "Summary: "
        f"{len(commands_run)} command(s), "
        f"{len(created_files)} file(s) created, "
        f"{len(modified_files)} file(s) modified."
    )
    lines.append(summary)

    return "\n".join(lines)


async def _attach_artifacts_from_text(
    text: str, session_id: str | None, message: cl.Message
) -> None:
    """Attach artifacts referenced in assistant text to a Chainlit message.

    Parameters
    ----------
    text : str
        Assistant output text to scan for file paths.
    session_id : str or None
        Current workspace session id.
    message : cl.Message
        Target message receiving attachments.

    Returns
    -------
    None
        Matching files are sent as elements when discovered.
    """

    if not session_id:
        return
    repo_dir = _resolve_workspaces_root() / session_id / "repo"
    if not repo_dir.exists():
        return
    repo_root = repo_dir.resolve()
    candidates = _extract_candidate_paths(text)
    if not candidates:
        return
    seen: set[str] = set()
    resolved_files: list[tuple[Path, Path]] = []
    for token in candidates:
        resolved = _resolve_artifact_path(repo_root, token)
        if not resolved:
            continue
        path, relative = resolved
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if MAX_ATTACHMENT_BYTES > 0:
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > MAX_ATTACHMENT_BYTES:
                LOGGER.info("Skipping large artifact %s (%d bytes)", path, size)
                continue
        resolved_files.append((path, relative))

    if not resolved_files:
        return

    image_files: list[tuple[Path, Path]] = []
    for path, relative in resolved_files:
        if path.suffix.lower() in IMAGE_EXTS:
            image_files.append((path, relative))

    if ZIP_MIN_COUNT > 0 and len(resolved_files) >= ZIP_MIN_COUNT:
        try:
            bundle_dir = repo_root / "outputs" / "_bundles"
            bundle_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            zip_path = bundle_dir / f"artifacts-{timestamp}.zip"
            import zipfile

            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for path, relative in resolved_files:
                    zf.write(path, arcname=relative.as_posix())

            if MAX_ATTACHMENT_BYTES > 0:
                zip_size = zip_path.stat().st_size
                if zip_size > MAX_ATTACHMENT_BYTES:
                    LOGGER.info("Skipping zip bundle %s (%d bytes)", zip_path, zip_size)
                    zip_path.unlink(missing_ok=True)
                    raise RuntimeError("Zip bundle exceeded attachment size limit.")

            zip_relative = zip_path.relative_to(repo_root)
            zip_element = cl.File(name=zip_relative.as_posix(), path=str(zip_path))
            await zip_element.send(for_id=message.id)

            for path, relative in image_files:
                element = _element_for_path(path, relative)
                await element.send(for_id=message.id)
            return
        except Exception as exc:
            LOGGER.exception("Failed to bundle artifacts: %s", exc)

    for path, relative in resolved_files:
        element = _element_for_path(path, relative)
        await element.send(for_id=message.id)


async def _stream_runner(payload: dict):
    """Stream SSE events from the runner `/run` endpoint.

    Parameters
    ----------
    payload : dict
        JSON payload forwarded to the runner service.

    Yields
    ------
    tuple[str, str]
        `(event_type, data)` frames parsed from SSE stream.
    """

    url = f"{RUNNER_URL}/run"
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            event_type = None
            data_lines: list[str] = []

            async for line in resp.aiter_lines():
                if line == "":
                    if data_lines:
                        data = "\n".join(data_lines)
                        yield event_type or "message", data
                    event_type = None
                    data_lines = []
                    continue

                if line.startswith("event:"):
                    event_type = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[len("data:") :].strip())

            if data_lines:
                data = "\n".join(data_lines)
                yield event_type or "message", data


def _build_runner_admission_params(
    session_id: str | None, user_id: str | None
) -> dict[str, str]:
    """Build runner admission query params from optional session/user identifiers."""

    params: dict[str, str] = {}
    if isinstance(session_id, str) and session_id.strip():
        params["session_id"] = session_id.strip()
    if isinstance(user_id, str) and user_id.strip():
        params["user_id"] = user_id.strip()
    return params


async def _probe_runner_admission(
    client: httpx.AsyncClient, *, session_id: str | None, user_id: str | None
) -> dict[str, Any] | None:
    """Fetch one runner admission readiness snapshot.

    Returns `None` when probing fails; callers should typically fail-open.
    """

    params = _build_runner_admission_params(session_id, user_id)
    headers: dict[str, str] | None = None
    if RUNNER_METRICS_TOKEN:
        headers = {"X-Runner-Metrics-Token": RUNNER_METRICS_TOKEN}
    try:
        response = await client.get(
            f"{RUNNER_URL}/ops/admission", params=params, headers=headers
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        LOGGER.warning("Runner admission probe failed: %s", exc)
        return None

    if isinstance(payload, dict):
        return payload
    return None


async def _wait_for_runner_admission_slot(
    *,
    session_id: str | None,
    user_id: str | None,
    on_queued: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Wait until runner can start a run immediately for this user/session.

    Parameters
    ----------
    session_id : str or None
        Current chat session identifier.
    user_id : str or None
        Authenticated user identifier, when available.
    on_queued : callable or None, optional
        Optional async callback invoked once when the first admission probe
        indicates this request must wait in queue.

    Returns
    -------
    dict[str, Any]
        Result payload with fields: `ok`, `waited`, `reason`, `wait_seconds`.
    """

    start = time.monotonic()
    timeout = ADMISSION_POLL_TIMEOUT_SECONDS
    interval = ADMISSION_POLL_INTERVAL_SECONDS

    async with httpx.AsyncClient(timeout=15.0) as client:
        first = await _probe_runner_admission(
            client, session_id=session_id, user_id=user_id
        )
        if first is None:
            return {
                "ok": True,
                "waited": False,
                "reason": "admission_probe_unavailable",
                "wait_seconds": 0.0,
            }
        if bool(first.get("can_run_now", True)):
            return {
                "ok": True,
                "waited": False,
                "reason": "admission_ready",
                "wait_seconds": 0.0,
            }
        if on_queued is not None:
            try:
                await on_queued(first)
            except Exception as exc:
                LOGGER.warning("Failed to emit queued admission notice: %s", exc)

        while True:
            elapsed = time.monotonic() - start
            if timeout > 0 and elapsed >= timeout:
                return {
                    "ok": False,
                    "waited": True,
                    "reason": "admission_timeout",
                    "wait_seconds": elapsed,
                }
            await asyncio.sleep(interval)

            snapshot = await _probe_runner_admission(
                client, session_id=session_id, user_id=user_id
            )
            if snapshot is None:
                return {
                    "ok": True,
                    "waited": True,
                    "reason": "admission_probe_unavailable",
                    "wait_seconds": time.monotonic() - start,
                }
            if bool(snapshot.get("can_run_now", True)):
                return {
                    "ok": True,
                    "waited": True,
                    "reason": "admission_ready",
                    "wait_seconds": time.monotonic() - start,
                }


def _runner_http_error_detail(exc: httpx.HTTPStatusError) -> str:
    """Extract a concise runner error detail from an HTTP failure."""

    response = exc.response
    if response is None:
        return ""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        if detail is not None:
            return str(detail)
    text = (response.text or "").strip()
    return text[:500] if text else ""


def _build_admission_queued_notice(snapshot: dict[str, Any]) -> str:
    """Build user-facing status text while waiting for admission."""

    per_user_active = snapshot.get("per_user_active")
    per_user_limit = snapshot.get("per_user_limit")
    active_total = snapshot.get("active_total")
    global_limit = snapshot.get("global_limit")

    if (
        isinstance(per_user_active, int)
        and isinstance(per_user_limit, int)
        and per_user_limit > 0
        and per_user_active >= per_user_limit
    ):
        return (
            f"[runner] Queued: you currently use "
            f"{per_user_active}/{per_user_limit} concurrent chats. "
            "This request will start automatically after one running chat "
            "finishes. No need to resend the message."
        )

    if (
        isinstance(active_total, int)
        and isinstance(global_limit, int)
        and global_limit > 0
        and active_total >= global_limit
    ):
        return (
            f"[runner] Queued: server concurrency is currently "
            f"{active_total}/{global_limit}. "
            "This request will start automatically when a slot is available. "
            "No need to resend the message."
        )

    return (
        "[runner] Queued: no execution slot is currently available. "
        "This request will start automatically when capacity is free. "
        "No need to resend the message."
    )


def _get_current_thread_id() -> str | None:
    """Return the active Chainlit thread identifier when available."""

    try:
        session = chainlit_context.session
    except Exception:
        return None
    thread_id = getattr(session, "thread_id", None)
    if isinstance(thread_id, str) and thread_id:
        return thread_id
    return None


def _should_surface_runner_log(text: str) -> bool:
    """Decide whether runner stderr log should be echoed into chat UI."""

    if not FORWARD_RUNNER_LOGS:
        return False
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    for marker in SUPPRESSED_RUNNER_LOG_MARKERS:
        if marker in lowered:
            return False
    return True


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
    prompt = _build_prompt(history, message.content)
    payload = {
        "session_id": session_id,
        "user_prompt": prompt,
        "sandbox": "workspace-write",
    }
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
