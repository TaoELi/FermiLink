from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("chainlit")

_TEMP_ROOT = tempfile.mkdtemp(prefix="fermilink-web-log-tests-")
os.environ.setdefault("CHAINLIT_APP_ROOT", _TEMP_ROOT)
os.environ.setdefault("SCIPKG_ROOT", str(Path(_TEMP_ROOT) / "scientific_packages"))
os.environ.setdefault("CHAINLIT_AUTH_SECRET", "test-secret")

from fermilink.web import app as web_app


def test_should_surface_runner_log_disabled_by_default(monkeypatch) -> None:
    monkeypatch.setattr(web_app, "FORWARD_RUNNER_LOGS", False)
    assert web_app._should_surface_runner_log("anything") is False


def test_should_surface_runner_log_suppresses_rollout_marker(monkeypatch) -> None:
    monkeypatch.setattr(web_app, "FORWARD_RUNNER_LOGS", True)
    text = (
        "2026-02-12T18:13:39.111802Z ERROR codex_core::rollout::list: "
        "state db missing rollout path for thread 123"
    )
    assert web_app._should_surface_runner_log(text) is False


def test_should_surface_runner_log_allows_non_suppressed_log(monkeypatch) -> None:
    monkeypatch.setattr(web_app, "FORWARD_RUNNER_LOGS", True)
    assert web_app._should_surface_runner_log("runner connected") is True
