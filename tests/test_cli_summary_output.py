from __future__ import annotations

from pathlib import Path

from fermilink import cli


def test_start_default_output_is_human_readable(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    runtime_root = tmp_path / "runtime"
    specs = {"runner": object(), "web": object()}
    names = ["runner", "web"]

    monkeypatch.setattr(cli, "resolve_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(cli, "_resolve_specs", lambda _components: (names, specs))
    monkeypatch.setattr(
        cli,
        "_ensure_bootstrap_package_for_services",
        lambda: {"status": "skipped", "reason": "packages_present"},
    )

    def fake_start(_runtime_root: Path, spec: object) -> dict[str, object]:
        if spec is specs["runner"]:
            return {
                "service": "runner",
                "status": "started",
                "pid": 101,
                "command": ["uvicorn", "x", "--host", "0.0.0.0", "--port", "8000"],
            }
        return {
            "service": "web",
            "status": "started",
            "pid": 102,
            "command": ["chainlit", "run", "x", "--host", "0.0.0.0", "--port", "7860"],
        }

    monkeypatch.setattr(cli, "start_service", fake_start)

    code = cli.main(["start"])
    assert code == 0

    out = capsys.readouterr().out
    assert "runner: started" in out
    assert "web: started" in out
    assert "port 8000" in out
    assert "port 7860" in out
    assert "{" not in out


def test_status_default_output_is_human_readable(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setattr(cli, "resolve_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(
        cli,
        "service_status",
        lambda _root, service: (
            {
                "service": "runner",
                "running": True,
                "pid": 10,
                "command": ["uvicorn", "x", "--host", "0.0.0.0", "--port", "8000"],
            }
            if service == "runner"
            else {"service": "web", "running": False, "reason": "stale_pid"}
        ),
    )

    code = cli.main(["status"])
    assert code == 0
    out = capsys.readouterr().out
    assert "runner: running" in out
    assert "port 8000" in out
    assert "web: not running (stale_pid)" in out
    assert "{" not in out
