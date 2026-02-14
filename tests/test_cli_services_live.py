from __future__ import annotations

import sys
from pathlib import Path

from fermilink import cli
from fermilink.services import service_status


def test_cli_start_restart_stop_with_real_processes(
    monkeypatch, tmp_path: Path
) -> None:
    fermilink_home = tmp_path / "fermilink-home"
    runtime_root = fermilink_home / "runtime"
    sleep_cmd = f'{sys.executable} -c "import time; time.sleep(120)"'

    monkeypatch.setenv("FERMILINK_HOME", str(fermilink_home))
    monkeypatch.setenv("FERMILINK_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("FERMILINK_CHAINLIT_APP_ROOT", str(fermilink_home))
    monkeypatch.setenv("FERMILINK_RUNNER_CMD", sleep_cmd)
    monkeypatch.setenv("FERMILINK_WEB_CMD", sleep_cmd)
    monkeypatch.setattr(
        cli,
        "_ensure_bootstrap_package_for_services",
        lambda: {"status": "skipped", "reason": "test"},
    )

    captured_payloads: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "_print_json", lambda payload: captured_payloads.append(payload))

    try:
        assert cli.main(["start", "--json"]) == 0
        runner_status = service_status(runtime_root, "runner")
        web_status = service_status(runtime_root, "web")
        assert runner_status["running"] is True
        assert web_status["running"] is True

        assert cli.main(["restart", "--json"]) == 0
        restart_payload = captured_payloads[-1]
        started = restart_payload.get("started")
        assert isinstance(started, list)
        assert len(started) == 2
        assert all(item.get("status") == "started" for item in started)

        runner_status = service_status(runtime_root, "runner")
        web_status = service_status(runtime_root, "web")
        assert runner_status["running"] is True
        assert web_status["running"] is True

        assert cli.main(["stop", "--json"]) == 0
        assert service_status(runtime_root, "runner")["running"] is False
        assert service_status(runtime_root, "web")["running"] is False
    finally:
        cli.main(["stop"])
