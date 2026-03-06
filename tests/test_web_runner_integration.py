from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

import httpx
import pytest

pytest.importorskip("chainlit")
pytest.importorskip("fastapi")

_TEMP_ROOT = tempfile.mkdtemp(prefix="fermilink-web-runner-tests-")
os.environ.setdefault("FERMILINK_CHAINLIT_APP_ROOT", _TEMP_ROOT)
os.environ.setdefault(
    "FERMILINK_SCIPKG_ROOT", str(Path(_TEMP_ROOT) / "scientific_packages")
)
os.environ.setdefault("FERMILINK_CHAINLIT_AUTH_SECRET", "test-secret")

from fermilink.runner import app as runner_app
from fermilink.runner.admission import RunAdmissionController
from fermilink.web import app as web_app


def _patch_minimal_runner_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspaces_root = tmp_path / "workspaces"
    source_dir = tmp_path / "software"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "README.md").write_text("template\n", encoding="utf-8")

    monkeypatch.setattr(runner_app, "_resolve_workspaces_root", lambda: workspaces_root)
    monkeypatch.setattr(runner_app, "_resolve_source_dir", lambda: source_dir)
    monkeypatch.setattr(runner_app, "resolve_scipkg_root", lambda: tmp_path / "scipkg")
    monkeypatch.setattr(
        runner_app, "bootstrap_legacy_maxwelllink_package", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        runner_app, "_ensure_template_agents_file", lambda *_a, **_k: None
    )
    monkeypatch.setattr(runner_app, "_ensure_git_repo", lambda *_a, **_k: None)
    monkeypatch.setattr(
        runner_app, "resolve_session_package", lambda **_k: (None, None)
    )


def _line_reader(lines: list[str]) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    for line in lines:
        reader.feed_data((line + "\n").encode("utf-8"))
    reader.feed_eof()
    return reader


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode = 0
        self.stdout = _line_reader(
            ['{"type":"agent_message","item":{"type":"agent_message"}}']
        )
        self.stderr = _line_reader(["runner-stderr-line"])

    async def wait(self) -> int:
        await asyncio.sleep(0)
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


def test_web_stream_runner_parses_runner_sse_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_repo = tmp_path / "workspaces" / "session-stream-test" / "repo"

    async def scenario() -> list[tuple[str, str]]:
        _patch_minimal_runner_env(monkeypatch, tmp_path)
        monkeypatch.setattr(runner_app, "DEFAULT_PROVIDER_BINARY_OVERRIDE", "python")
        monkeypatch.setattr(
            runner_app,
            "_resolve_run_policy",
            lambda _req: ("codex", "enforce", "read-only", None, None),
        )
        monkeypatch.setattr(
            runner_app,
            "RUN_ADMISSION_CONTROLLER",
            RunAdmissionController(global_limit=2, per_user_limit=1, max_queue_size=2),
        )

        captured_cmd: list[str] = []

        async def fake_create_subprocess_exec(*cmd, **_kwargs):
            captured_cmd[:] = [str(token) for token in cmd]
            return _FakeProcess()

        monkeypatch.setattr(
            runner_app.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
        )

        class _ASGIClient(httpx.AsyncClient):
            def __init__(self, *args, **kwargs):
                kwargs.setdefault("base_url", "http://runner")
                kwargs.setdefault("transport", httpx.ASGITransport(app=runner_app.app))
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(web_app.httpx, "AsyncClient", _ASGIClient)
        monkeypatch.setattr(web_app, "RUNNER_URL", "http://runner")

        events: list[tuple[str, str]] = []
        async for event_type, data in web_app._stream_runner(
            {
                "session_id": "session-stream-test",
                "user_prompt": "hello from web",
                "sandbox": "read-only",
            }
        ):
            events.append((event_type, data))

        assert captured_cmd
        assert captured_cmd[0] == "python"
        assert captured_cmd[-1] == "hello from web"
        return events

    events = asyncio.run(scenario())
    event_types = [event_type for event_type, _ in events]

    assert event_types[0] == "meta"
    assert "agent" in event_types
    assert "log" in event_types
    assert event_types[-1] == "runner.exit"

    meta_payload = json.loads(events[0][1])
    assert meta_payload["session_id"] == "session-stream-test"

    agent_payloads = [
        json.loads(data) for event_type, data in events if event_type == "agent"
    ]
    assert any(payload.get("type") == "agent_message" for payload in agent_payloads)

    log_payload = json.loads(
        next(data for event_type, data in events if event_type == "log")
    )
    assert log_payload["text"] == "runner-stderr-line"
    memory_path = workspace_repo / "projects" / "memory.md"
    assert memory_path.is_file()
    assert "hello from web" in memory_path.read_text(encoding="utf-8")

    exit_payload = json.loads(events[-1][1])
    assert exit_payload["reason"] == "completed"
    assert exit_payload["return_code"] == 0


def test_web_stream_runner_handles_large_provider_json_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    large_text = "x" * 70_000
    large_event = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": large_text}]},
        }
    )

    class _LargeLineProcess:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = _line_reader([large_event])
            self.stderr = _line_reader([])

        async def wait(self) -> int:
            await asyncio.sleep(0)
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

    async def scenario() -> list[tuple[str, str]]:
        _patch_minimal_runner_env(monkeypatch, tmp_path)
        monkeypatch.setattr(runner_app, "DEFAULT_PROVIDER_BINARY_OVERRIDE", "python")
        monkeypatch.setattr(
            runner_app,
            "_resolve_run_policy",
            lambda _req: ("claude", "enforce", "read-only", None, None),
        )
        monkeypatch.setattr(
            runner_app,
            "RUN_ADMISSION_CONTROLLER",
            RunAdmissionController(global_limit=2, per_user_limit=1, max_queue_size=2),
        )

        async def fake_create_subprocess_exec(*_cmd, **_kwargs):
            return _LargeLineProcess()

        monkeypatch.setattr(
            runner_app.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
        )

        class _ASGIClient(httpx.AsyncClient):
            def __init__(self, *args, **kwargs):
                kwargs.setdefault("base_url", "http://runner")
                kwargs.setdefault("transport", httpx.ASGITransport(app=runner_app.app))
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(web_app.httpx, "AsyncClient", _ASGIClient)
        monkeypatch.setattr(web_app, "RUNNER_URL", "http://runner")

        events: list[tuple[str, str]] = []
        async for event_type, data in web_app._stream_runner(
            {
                "session_id": "session-large-line-test",
                "user_prompt": "hello from web",
                "sandbox": "read-only",
            }
        ):
            events.append((event_type, data))
        return events

    events = asyncio.run(scenario())
    agent_payload = next(
        json.loads(data) for event_type, data in events if event_type == "agent"
    )

    assert agent_payload["message"]["content"][0]["text"] == large_text
    assert json.loads(events[-1][1])["reason"] == "completed"
