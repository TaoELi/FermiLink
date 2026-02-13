from __future__ import annotations

from pathlib import Path

from fermilink import cli


def test_start_aborts_after_first_failed_component(
    monkeypatch, tmp_path: Path
) -> None:
    runtime_root = tmp_path / "runtime"
    specs = {"runner": object(), "web": object()}
    names = ["runner", "web"]
    start_calls: list[str] = []
    payloads: list[dict[str, object]] = []

    monkeypatch.setattr(cli, "resolve_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(cli, "_resolve_specs", lambda _components: (names, specs))
    monkeypatch.setattr(
        cli,
        "_ensure_bootstrap_package_for_services",
        lambda: {"status": "skipped", "reason": "test"},
    )

    def fake_start(_runtime_root: Path, spec: object) -> dict[str, object]:
        service = "runner" if spec is specs["runner"] else "web"
        start_calls.append(service)
        if service == "runner":
            return {"service": "runner", "status": "port_in_use"}
        return {"service": "web", "status": "started"}

    monkeypatch.setattr(cli, "start_service", fake_start)
    monkeypatch.setattr(cli, "_print_json", lambda payload: payloads.append(payload))

    code = cli.main(["start"])

    assert code == 2
    assert start_calls == ["runner"]
    assert len(payloads) == 1
    results = payloads[0]["results"]
    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0]["status"] == "port_in_use"


def test_start_rolls_back_started_components_on_later_failure(
    monkeypatch, tmp_path: Path
) -> None:
    runtime_root = tmp_path / "runtime"
    specs = {"runner": object(), "web": object()}
    names = ["runner", "web"]
    start_calls: list[str] = []
    stop_calls: list[str] = []
    payloads: list[dict[str, object]] = []

    monkeypatch.setattr(cli, "resolve_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(cli, "_resolve_specs", lambda _components: (names, specs))
    monkeypatch.setattr(
        cli,
        "_ensure_bootstrap_package_for_services",
        lambda: {"status": "skipped", "reason": "test"},
    )

    def fake_start(_runtime_root: Path, spec: object) -> dict[str, object]:
        service = "runner" if spec is specs["runner"] else "web"
        start_calls.append(service)
        if service == "runner":
            return {"service": "runner", "status": "started"}
        return {"service": "web", "status": "failed_to_start"}

    def fake_stop(_runtime_root: Path, service: str) -> dict[str, object]:
        stop_calls.append(service)
        return {"service": service, "status": "stopped"}

    monkeypatch.setattr(cli, "start_service", fake_start)
    monkeypatch.setattr(cli, "stop_service", fake_stop)
    monkeypatch.setattr(cli, "_print_json", lambda payload: payloads.append(payload))

    code = cli.main(["start"])

    assert code == 2
    assert start_calls == ["runner", "web"]
    assert stop_calls == ["runner"]
    rollback = payloads[0].get("rollback")
    assert isinstance(rollback, list)
    assert rollback[0]["service"] == "runner"


def test_restart_stops_all_before_starting_and_aborts_on_failure(
    monkeypatch, tmp_path: Path
) -> None:
    runtime_root = tmp_path / "runtime"
    specs = {"runner": object(), "web": object()}
    names = ["runner", "web"]
    stop_calls: list[str] = []
    start_calls: list[str] = []
    payloads: list[dict[str, object]] = []

    monkeypatch.setattr(cli, "resolve_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(cli, "_resolve_specs", lambda _components: (names, specs))
    monkeypatch.setattr(
        cli,
        "_ensure_bootstrap_package_for_services",
        lambda: {"status": "skipped", "reason": "test"},
    )

    def fake_stop(_runtime_root: Path, service: str) -> dict[str, object]:
        stop_calls.append(service)
        return {"service": service, "status": "stopped"}

    def fake_start(_runtime_root: Path, spec: object) -> dict[str, object]:
        service = "runner" if spec is specs["runner"] else "web"
        start_calls.append(service)
        if service == "runner":
            return {"service": "runner", "status": "port_in_use"}
        return {"service": "web", "status": "started"}

    monkeypatch.setattr(cli, "stop_service", fake_stop)
    monkeypatch.setattr(cli, "start_service", fake_start)
    monkeypatch.setattr(cli, "_print_json", lambda payload: payloads.append(payload))

    code = cli.main(["restart"])

    assert code == 2
    assert stop_calls == ["runner", "web"]
    assert start_calls == ["runner"]
    started = payloads[0]["started"]
    assert isinstance(started, list)
    assert len(started) == 1
    assert started[0]["status"] == "port_in_use"
