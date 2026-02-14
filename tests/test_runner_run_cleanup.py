from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fermilink.runner import app as runner_app
from fermilink.runner.admission import RunAdmissionController


def _patch_minimal_runner_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Patch runner dependencies so tests can focus on cancellation cleanup."""

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
    monkeypatch.setattr(runner_app, "_ensure_template_agents_file", lambda *_a, **_k: None)
    monkeypatch.setattr(runner_app, "_ensure_git_repo", lambda *_a, **_k: None)
    monkeypatch.setattr(runner_app, "resolve_session_package", lambda **_k: (None, None))


def test_cancel_during_setup_releases_admission_slot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def scenario() -> None:
        _patch_minimal_runner_env(monkeypatch, tmp_path)
        controller = RunAdmissionController(
            global_limit=1, per_user_limit=1, max_queue_size=10
        )
        monkeypatch.setattr(runner_app, "RUN_ADMISSION_CONTROLLER", controller)

        started = asyncio.Event()
        gate = asyncio.Event()

        async def fake_create_subprocess_exec(*_a, **_k):
            started.set()
            await gate.wait()
            raise AssertionError("gate should never open in this test")

        monkeypatch.setattr(
            runner_app.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
        )

        req = runner_app.RunRequest(
            session_id="s-setup-cancel",
            user_id="alice",
            user_prompt="hello",
            sandbox="read-only",
        )
        run_task = asyncio.create_task(runner_app.run(req))

        await asyncio.wait_for(started.wait(), timeout=0.5)
        snapshot = await controller.snapshot()
        assert snapshot["active_total"] == 1
        assert snapshot["pending_total"] == 0

        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task

        snapshot = await controller.snapshot()
        assert snapshot["active_total"] == 0
        assert snapshot["pending_total"] == 0

    asyncio.run(scenario())


def test_cancel_streaming_response_releases_admission_slot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class StuckProcess:
        def __init__(self) -> None:
            self.returncode = None
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self._done = asyncio.Event()

        async def wait(self) -> int:
            await self._done.wait()
            if self.returncode is None:
                self.returncode = -1
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15
            self._done.set()

        def kill(self) -> None:
            self.returncode = -9
            self._done.set()

    async def scenario() -> None:
        _patch_minimal_runner_env(monkeypatch, tmp_path)
        controller = RunAdmissionController(
            global_limit=1, per_user_limit=1, max_queue_size=10
        )
        monkeypatch.setattr(runner_app, "RUN_ADMISSION_CONTROLLER", controller)

        async def fake_create_subprocess_exec(*_a, **_k):
            return StuckProcess()

        monkeypatch.setattr(
            runner_app.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
        )

        req = runner_app.RunRequest(
            session_id="s-stream-cancel",
            user_id="alice",
            user_prompt="hello",
            sandbox="read-only",
        )
        response = await runner_app.run(req)

        saw_meta = asyncio.Event()

        async def consume() -> None:
            async for _chunk in response.body_iterator:
                saw_meta.set()
                await asyncio.sleep(1)

        consumer_task = asyncio.create_task(consume())
        await asyncio.wait_for(saw_meta.wait(), timeout=0.5)

        snapshot = await controller.snapshot()
        assert snapshot["active_total"] == 1
        assert snapshot["pending_total"] == 0

        consumer_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer_task

        close_iter = getattr(response.body_iterator, "aclose", None)
        if callable(close_iter):
            await close_iter()

        deadline = asyncio.get_running_loop().time() + 2.0
        while True:
            snapshot = await controller.snapshot()
            if snapshot["active_total"] == 0 and snapshot["pending_total"] == 0:
                break
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(0.01)

        snapshot = await controller.snapshot()
        assert snapshot["active_total"] == 0
        assert snapshot["pending_total"] == 0

    asyncio.run(scenario())


def test_resolve_source_dir_prefers_packaged_software(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = tmp_path / "missing-software"
    monkeypatch.setenv("FERMILINK_SOFTWARE_ROOT", str(missing))

    source_dir = runner_app._resolve_source_dir()
    assert source_dir.exists()
    assert source_dir == runner_app.PACKAGE_SOFTWARE_ROOT
