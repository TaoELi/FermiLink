from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fermilink import services
from fermilink.services import (
    ServiceSpec,
    default_service_specs,
    service_status,
    start_service,
    stop_service,
)


def test_start_and_stop_service(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    spec = ServiceSpec(
        name="runner",
        command=[sys.executable, "-c", "import time; time.sleep(60)"],
        env={},
    )

    started = start_service(runtime_root, spec)
    assert started["status"] in {"started", "already_running"}

    status = service_status(runtime_root, "runner")
    assert status["running"] is True

    stopped = stop_service(runtime_root, "runner")
    assert stopped["status"] == "stopped"

    status_after = service_status(runtime_root, "runner")
    assert status_after["running"] is False


def test_default_service_specs_include_chainlit_app_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RUNNER_URL", "http://127.0.0.1:18000")
    monkeypatch.setenv("CHAINLIT_APP_ROOT", str(tmp_path / "app-root"))
    monkeypatch.delenv("SCIPKG_ROOT", raising=False)
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    specs = default_service_specs(web_app_path=tmp_path / "web" / "app.py")

    web_env = specs["web"].env
    runner_env = specs["runner"].env
    app_root = (tmp_path / "app-root").resolve()
    assert web_env["RUNNER_URL"] == "http://127.0.0.1:18000"
    assert web_env["CHAINLIT_APP_ROOT"] == str(app_root)
    assert web_env["SCIPKG_ROOT"] == str(app_root / "scientific_packages")
    assert web_env["WORKSPACES_ROOT"] == str(app_root / "workspaces")
    assert "CODEX_HOME" not in web_env
    assert runner_env["SCIPKG_ROOT"] == str(app_root / "scientific_packages")
    assert runner_env["WORKSPACES_ROOT"] == str(app_root / "workspaces")
    assert "CODEX_HOME" not in runner_env


def test_default_service_specs_uses_fermilink_home_defaults(
    monkeypatch, tmp_path: Path
) -> None:
    fermilink_home = (tmp_path / "fl-home").resolve()
    monkeypatch.setenv("FERMILINK_HOME", str(fermilink_home))
    monkeypatch.delenv("CHAINLIT_APP_ROOT", raising=False)
    monkeypatch.delenv("SCIPKG_ROOT", raising=False)
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)

    specs = default_service_specs(web_app_path=tmp_path / "web" / "app.py")
    web_env = specs["web"].env
    runner_env = specs["runner"].env

    assert web_env["CHAINLIT_APP_ROOT"] == str(fermilink_home)
    assert web_env["SCIPKG_ROOT"] == str(fermilink_home / "scientific_packages")
    assert web_env["WORKSPACES_ROOT"] == str(fermilink_home / "workspaces")
    assert runner_env["SCIPKG_ROOT"] == str(fermilink_home / "scientific_packages")
    assert runner_env["WORKSPACES_ROOT"] == str(fermilink_home / "workspaces")


def test_default_service_specs_respects_explicit_codex_home(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CHAINLIT_APP_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    specs = default_service_specs(web_app_path=tmp_path / "web" / "app.py")

    expected = str((tmp_path / "codex-home").resolve())
    assert specs["runner"].env["CODEX_HOME"] == expected
    assert specs["web"].env["CODEX_HOME"] == expected


def test_start_service_reports_immediate_failure(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    spec = ServiceSpec(
        name="runner",
        command=[sys.executable, "-c", "import sys; sys.exit(3)"],
        env={},
    )

    started = start_service(runtime_root, spec)
    assert started["status"] == "failed_to_start"
    assert started["exit_code"] == 3

    status = service_status(runtime_root, "runner")
    assert status["running"] is False
    assert status["reason"] == "no_state"


def test_start_service_reports_port_in_use(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    port = 17860
    monkeypatch.setattr(services, "_is_port_in_use", lambda *_a, **_k: True)

    spec = ServiceSpec(
        name="web",
        command=["dummy-process", "--host", "127.0.0.1", "--port", str(port)],
        env={},
    )
    started = start_service(runtime_root, spec)

    assert started["status"] == "port_in_use"
    assert started["port"] == port
    status = service_status(runtime_root, "web")
    assert status["running"] is False
    assert status["reason"] == "no_state"
