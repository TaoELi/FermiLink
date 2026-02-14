from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

import httpx


async def _stream_runner(
    payload: dict[str, Any],
    *,
    runner_url: str,
    httpx_module: Any = httpx,
):
    """Stream SSE events from the runner `/run` endpoint."""

    url = f"{runner_url}/run"
    async with httpx_module.AsyncClient(timeout=None) as client:
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
    session_id: str | None,
    user_id: str | None,
) -> dict[str, str]:
    """Build runner admission query params from optional session/user identifiers."""

    params: dict[str, str] = {}
    if isinstance(session_id, str) and session_id.strip():
        params["session_id"] = session_id.strip()
    if isinstance(user_id, str) and user_id.strip():
        params["user_id"] = user_id.strip()
    return params


async def _probe_runner_admission(
    client: httpx.AsyncClient,
    *,
    runner_url: str,
    session_id: str | None,
    user_id: str | None,
    runner_metrics_token: str,
    logger: logging.Logger,
) -> dict[str, Any] | None:
    """Fetch one runner admission readiness snapshot."""

    params = _build_runner_admission_params(session_id, user_id)
    headers: dict[str, str] | None = None
    if runner_metrics_token:
        headers = {"X-Runner-Metrics-Token": runner_metrics_token}
    try:
        response = await client.get(
            f"{runner_url}/ops/admission", params=params, headers=headers
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("Runner admission probe failed: %s", exc)
        return None

    if isinstance(payload, dict):
        return payload
    return None


async def _wait_for_runner_admission_slot(
    *,
    session_id: str | None,
    user_id: str | None,
    on_queued: Callable[[dict[str, Any]], Awaitable[None]] | None,
    admission_poll_timeout_seconds: float,
    admission_poll_interval_seconds: float,
    runner_url: str,
    runner_metrics_token: str,
    logger: logging.Logger,
    httpx_module: Any = httpx,
) -> dict[str, Any]:
    """Wait until runner can start a run immediately for this user/session."""

    start = time.monotonic()
    timeout = admission_poll_timeout_seconds
    interval = admission_poll_interval_seconds

    async with httpx_module.AsyncClient(timeout=15.0) as client:
        first = await _probe_runner_admission(
            client,
            runner_url=runner_url,
            session_id=session_id,
            user_id=user_id,
            runner_metrics_token=runner_metrics_token,
            logger=logger,
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
                logger.warning("Failed to emit queued admission notice: %s", exc)

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
                client,
                runner_url=runner_url,
                session_id=session_id,
                user_id=user_id,
                runner_metrics_token=runner_metrics_token,
                logger=logger,
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


def _should_surface_runner_log(
    text: str,
    *,
    forward_runner_logs: bool,
    suppressed_runner_log_markers: tuple[str, ...],
) -> bool:
    """Decide whether runner stderr log should be echoed into chat UI."""

    if not forward_runner_logs:
        return False
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    for marker in suppressed_runner_log_markers:
        if marker in lowered:
            return False
    return True
