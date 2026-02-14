from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest
from fastapi import HTTPException

pytest.importorskip("chainlit")

_TEMP_ROOT = tempfile.mkdtemp(prefix="fermilink-web-auth-tests-")
os.environ.setdefault("FERMILINK_CHAINLIT_APP_ROOT", _TEMP_ROOT)
os.environ.setdefault(
    "FERMILINK_SCIPKG_ROOT", str(Path(_TEMP_ROOT) / "scientific_packages")
)
os.environ.setdefault("FERMILINK_CHAINLIT_AUTH_SECRET", "test-secret")

from chainlit.auth import jwt as chainlit_jwt
from chainlit.user import User
from fermilink.web import app as web_app


def _prepare_auth_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    auth_db_path = tmp_path / ".chainlit" / "auth.db"
    web_app._ensure_sqlite_schema(auth_db_path, web_app.AUTH_SCHEMA_SQL)
    web_app._ensure_sqlite_columns(
        auth_db_path,
        "auth_users",
        {
            "group_name": "TEXT",
        },
    )
    monkeypatch.setattr(web_app, "AUTH_DB_PATH", auth_db_path)
    return auth_db_path


def test_signup_api_then_password_auth_callback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare_auth_db(monkeypatch, tmp_path)
    monkeypatch.setattr(web_app, "AUTH_SIGNUP_ENABLED", True)
    monkeypatch.setattr(web_app, "AUTH_MAX_USERS", 0)

    payload = {
        "email": "new-user@example.com",
        "password": "strong-password",
        "confirm_password": "strong-password",
    }
    result = asyncio.run(web_app.signup_api(payload))
    assert result["ok"] is True
    assert result["identifier"] == "new-user@example.com"

    user = web_app.auth_callback("new-user@example.com", "strong-password")
    assert user is not None
    assert user.identifier == "new-user@example.com"


def test_signup_api_rejects_duplicate_user(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare_auth_db(monkeypatch, tmp_path)
    monkeypatch.setattr(web_app, "AUTH_SIGNUP_ENABLED", True)
    monkeypatch.setattr(web_app, "AUTH_MAX_USERS", 0)

    payload = {
        "email": "dupe@example.com",
        "password": "duplicate-password",
        "confirm_password": "duplicate-password",
    }
    first = asyncio.run(web_app.signup_api(payload))
    assert first["ok"] is True

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(web_app.signup_api(payload))
    assert exc_info.value.status_code == 409
    assert "already registered" in str(exc_info.value.detail).lower()


def test_jwt_token_invalid_after_secret_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CHAINLIT_AUTH_SECRET", "secret-one-with-at-least-thirty-two-bytes"
    )
    token = chainlit_jwt.create_jwt(
        User(identifier="token-user@example.com", metadata={"provider": "password"})
    )

    decoded = chainlit_jwt.decode_jwt(token)
    assert decoded.identifier == "token-user@example.com"

    monkeypatch.setenv(
        "CHAINLIT_AUTH_SECRET", "secret-two-with-at-least-thirty-two-bytes"
    )
    with pytest.raises(Exception):
        chainlit_jwt.decode_jwt(token)
