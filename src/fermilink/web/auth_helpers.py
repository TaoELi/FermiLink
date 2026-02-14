from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo


def _normalize_username(username: str) -> str:
    """Normalize login usernames for stable auth lookups."""

    return username.strip().lower()


def _validate_password(password: str, *, auth_min_password_len: int) -> bool:
    """Validate password length against configured minimum."""

    return len(password) >= auth_min_password_len


def _validate_email(email: str, *, email_pattern: Any) -> bool:
    """Validate login/signup identifier as an email-like string."""

    return bool(email_pattern.fullmatch(email))


def _count_auth_users(*, open_auth_db: Callable[[], sqlite3.Connection]) -> int:
    """Count registered users in the auth database."""

    with open_auth_db() as conn:
        row = conn.execute("SELECT COUNT(*) AS user_count FROM auth_users").fetchone()
    if row is None:
        return 0
    return int(row["user_count"] or 0)


def _signup_status_payload(
    *,
    count_auth_users: Callable[[], int],
    auth_signup_enabled: bool,
    auth_max_users: int,
    auth_min_password_len: int,
) -> dict[str, Any]:
    """Build current self-signup policy payload for API/UI."""

    user_count = count_auth_users()
    max_users = auth_max_users if auth_max_users > 0 else None
    enabled = bool(auth_signup_enabled)
    reason: str | None = None
    message = "Self sign-up is available."

    if not auth_signup_enabled:
        enabled = False
        reason = "disabled_by_admin"
        message = "Sign up is disabled by admin."
    elif auth_max_users > 0 and user_count >= auth_max_users:
        enabled = False
        reason = "user_limit_reached"
        message = f"Sign up is closed. User limit reached ({auth_max_users})."

    return {
        "enabled": enabled,
        "reason": reason,
        "message": message,
        "user_count": user_count,
        "max_users": max_users,
        "min_password_length": auth_min_password_len,
    }


def _normalize_group(group_name: str | None, *, default_prompt_group: str) -> str:
    """Normalize prompt-quota group name with default fallback."""

    if not group_name:
        return default_prompt_group
    normalized = str(group_name).strip().lower()
    return normalized or default_prompt_group


def _get_prompt_timezone(*, prompt_day_tz: str, logger: Any) -> timezone:
    """Resolve timezone used for daily prompt-limit windows."""

    if prompt_day_tz.upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(prompt_day_tz)
    except Exception:
        logger.warning("Invalid PROMPT_DAY_TZ=%r, defaulting to UTC", prompt_day_tz)
        return timezone.utc


def _current_usage_day(
    now: datetime | None = None,
    *,
    get_prompt_timezone: Callable[[], timezone],
) -> tuple[str, datetime]:
    """Compute current usage-day key in configured quota timezone."""

    tz = get_prompt_timezone()
    now = now.astimezone(tz) if now else datetime.now(tz)
    return now.date().isoformat(), now


def _next_usage_reset(
    now: datetime | None = None,
    *,
    current_usage_day: Callable[[datetime | None], tuple[str, datetime]],
) -> datetime:
    """Compute next quota reset timestamp (next local midnight)."""

    _, current = current_usage_day(now)
    next_midnight = (current + timedelta(days=1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return next_midnight


def _get_prompt_limit(
    group_name: str,
    *,
    normalize_group: Callable[[str | None], str],
    prompt_limits: dict[str, int],
    default_prompt_group: str,
) -> int:
    """Return the daily prompt limit for a user group."""

    normalized = normalize_group(group_name)
    limit = prompt_limits.get(normalized)
    if limit is None:
        limit = prompt_limits.get(default_prompt_group, 0)
    return limit


def _get_user_group(
    username: str,
    *,
    normalize_username: Callable[[str], str],
    default_prompt_group: str,
    open_auth_db: Callable[[], sqlite3.Connection],
    normalize_group: Callable[[str | None], str],
) -> str:
    """Lookup a user's quota group from the auth database."""

    normalized = normalize_username(username)
    if not normalized:
        return default_prompt_group
    with open_auth_db() as conn:
        row = conn.execute(
            "SELECT group_name FROM auth_users WHERE username = ?",
            (normalized,),
        ).fetchone()
    if row is None:
        return default_prompt_group
    return normalize_group(row["group_name"])


def _consume_prompt_quota(
    username: str,
    *,
    normalize_username: Callable[[str], str],
    get_user_group: Callable[[str], str],
    get_prompt_limit: Callable[[str], int],
    default_prompt_group: str,
    next_usage_reset: Callable[[datetime | None], datetime],
    current_usage_day: Callable[[datetime | None], tuple[str, datetime]],
    open_auth_db: Callable[[], sqlite3.Connection],
) -> tuple[bool, int, int, str, datetime]:
    """Consume one prompt quota unit for a user when allowed."""

    normalized = normalize_username(username)
    if not normalized:
        return True, 0, 0, default_prompt_group, next_usage_reset(None)

    group_name = get_user_group(normalized)
    limit = get_prompt_limit(group_name)
    if limit <= 0:
        return True, 0, limit, group_name, next_usage_reset(None)

    usage_day, now = current_usage_day(None)
    reset_at = next_usage_reset(now)
    now_iso = now.isoformat()

    with open_auth_db() as conn:
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


def _open_auth_db(*, auth_db_path: Any) -> sqlite3.Connection:
    """Open the auth sqlite database with row and FK settings enabled."""

    conn = sqlite3.connect(auth_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _get_user(
    username: str,
    *,
    normalize_username: Callable[[str], str],
    open_auth_db: Callable[[], sqlite3.Connection],
) -> sqlite3.Row | None:
    """Fetch an auth user row by username."""

    normalized = normalize_username(username)
    if not normalized:
        return None
    with open_auth_db() as conn:
        row = conn.execute(
            "SELECT username, password_hash, created_at, last_login "
            "FROM auth_users WHERE username = ?",
            (normalized,),
        ).fetchone()
    return row


def _create_user(
    username: str,
    password: str,
    *,
    normalize_username: Callable[[str], str],
    validate_password: Callable[[str], bool],
    open_auth_db: Callable[[], sqlite3.Connection],
    default_prompt_group: str,
    get_user: Callable[[str], sqlite3.Row | None],
    pbkdf2_sha256: Any,
) -> sqlite3.Row | None:
    """Create a new auth user account with hashed password."""

    normalized = normalize_username(username)
    if not normalized:
        return None
    if not validate_password(password):
        return None
    now = datetime.now(timezone.utc).isoformat()
    password_hash = pbkdf2_sha256.hash(password)
    try:
        with open_auth_db() as conn:
            conn.execute(
                "INSERT INTO auth_users (username, password_hash, created_at, group_name) "
                "VALUES (?, ?, ?, ?)",
                (normalized, password_hash, now, default_prompt_group),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        return None
    return get_user(normalized)


def _register_signup_user(
    username: str,
    password: str,
    *,
    normalize_username: Callable[[str], str],
    validate_email: Callable[[str], bool],
    validate_password: Callable[[str], bool],
    open_auth_db: Callable[[], sqlite3.Connection],
    auth_signup_enabled: bool,
    auth_max_users: int,
    default_prompt_group: str,
    get_user: Callable[[str], sqlite3.Row | None],
    pbkdf2_sha256: Any,
    logger: Any,
) -> tuple[sqlite3.Row | None, str]:
    """Register a user while enforcing self-signup policy atomically."""

    normalized = normalize_username(username)
    if not normalized or not validate_email(normalized):
        return None, "invalid_username"
    if not validate_password(password):
        return None, "invalid_password"

    now = datetime.now(timezone.utc).isoformat()
    try:
        with open_auth_db() as conn:
            conn.execute("BEGIN IMMEDIATE")

            if not auth_signup_enabled:
                conn.rollback()
                return None, "disabled_by_admin"

            if auth_max_users > 0:
                row = conn.execute(
                    "SELECT COUNT(*) AS user_count FROM auth_users"
                ).fetchone()
                count = int(row["user_count"] or 0) if row else 0
                if count >= auth_max_users:
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
                (normalized, password_hash, now, default_prompt_group),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        return None, "already_exists"
    except sqlite3.Error:
        logger.exception("Failed to register signup user %s", normalized)
        return None, "db_error"

    created = get_user(normalized)
    if created is None:
        return None, "db_error"
    return created, "created"


def _update_last_login(
    username: str,
    *,
    open_auth_db: Callable[[], sqlite3.Connection],
) -> None:
    """Update the `last_login` timestamp for a user."""

    now = datetime.now(timezone.utc).isoformat()
    with open_auth_db() as conn:
        conn.execute(
            "UPDATE auth_users SET last_login = ? WHERE username = ?",
            (now, username),
        )
        conn.commit()


def _authenticate_user(
    username: str,
    password: str,
    *,
    normalize_username: Callable[[str], str],
    get_user: Callable[[str], sqlite3.Row | None],
    auth_auto_register: bool,
    create_user: Callable[[str, str], sqlite3.Row | None],
    update_last_login: Callable[[str], None],
    pbkdf2_sha256: Any,
) -> str | None:
    """Authenticate a user and optionally auto-register unknown users."""

    normalized = normalize_username(username)
    if not normalized or not password:
        return None
    user_row = get_user(normalized)
    if user_row is None:
        if not auth_auto_register:
            return None
        created = create_user(normalized, password)
        if created is None:
            return None
        update_last_login(normalized)
        return normalized

    if not pbkdf2_sha256.verify(password, user_row["password_hash"]):
        return None

    update_last_login(normalized)
    return normalized
