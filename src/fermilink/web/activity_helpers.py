from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable


def _get_current_user_identifier(*, user_session: Any) -> str | None:
    """Extract current Chainlit user identifier from session context."""

    user = user_session.get("user")
    if not user:
        return None
    if isinstance(user, dict):
        identifier = user.get("identifier")
        return identifier if isinstance(identifier, str) and identifier else None
    identifier = getattr(user, "identifier", None)
    return identifier if isinstance(identifier, str) and identifier else None


def _get_current_chainlit_session_identifier(*, chainlit_context: Any) -> str | None:
    """Extract current Chainlit websocket session identifier when available."""

    try:
        session = chainlit_context.session
    except Exception:
        return None

    candidate = getattr(session, "id", None)
    if isinstance(candidate, str) and candidate:
        return candidate

    candidate = getattr(session, "session_id", None)
    if isinstance(candidate, str) and candidate:
        return candidate
    return None


def _get_current_chainlit_session_object(*, chainlit_context: Any) -> Any | None:
    """Return current Chainlit session object when available."""

    try:
        return chainlit_context.session
    except Exception:
        return None


def _get_current_thread_id(*, chainlit_context: Any) -> str | None:
    """Return the active Chainlit thread identifier when available."""

    try:
        session = chainlit_context.session
    except Exception:
        return None
    thread_id = getattr(session, "thread_id", None)
    if isinstance(thread_id, str) and thread_id:
        return thread_id
    return None


def _resolve_activity_owner_keys(
    user_identifier: str | None = None,
    *,
    get_current_user_identifier: Callable[[], str | None],
    get_current_chainlit_session_object: Callable[[], Any | None],
    get_current_chainlit_session_identifier: Callable[[], str | None],
) -> list[str]:
    """Resolve owner keys used for in-memory active thread tracking."""

    keys: list[str] = []
    cleaned_user = None
    if isinstance(user_identifier, str) and user_identifier.strip():
        cleaned_user = user_identifier.strip().lower()
    elif isinstance(user_identifier, str):
        cleaned_user = None
    else:
        current_user = get_current_user_identifier()
        if isinstance(current_user, str) and current_user.strip():
            cleaned_user = current_user.strip().lower()
        else:
            session = get_current_chainlit_session_object()
            if session is not None:
                session_user = getattr(session, "user", None)
                if isinstance(session_user, dict):
                    identifier = session_user.get("identifier")
                else:
                    identifier = getattr(session_user, "identifier", None)
                if isinstance(identifier, str) and identifier.strip():
                    cleaned_user = identifier.strip().lower()

    if cleaned_user:
        keys.append(f"user:{cleaned_user}")

    session_identifier = get_current_chainlit_session_identifier()
    if isinstance(session_identifier, str) and session_identifier:
        keys.append(f"session:{session_identifier}")
    return keys


def _owner_scope_matches(
    binding_owner_keys: set[str],
    candidate_owner_keys: set[str],
) -> bool:
    """Check whether an active-run binding belongs to current requester scope."""

    if binding_owner_keys & candidate_owner_keys:
        return True
    binding_has_user = any(key.startswith("user:") for key in binding_owner_keys)
    candidate_has_user = any(key.startswith("user:") for key in candidate_owner_keys)
    if not binding_has_user and not candidate_has_user:
        return True
    return False


async def _thread_has_active_run_for_owner(
    thread_id: str | None,
    owner_keys: list[str],
    *,
    active_threads_lock: asyncio.Lock,
    active_runs_by_thread: dict[str, Any],
    owner_scope_matches: Callable[[set[str], set[str]], bool],
) -> bool:
    """Return whether one thread currently has an in-flight run for this owner."""

    if not thread_id:
        return False
    candidate_keys = {key for key in owner_keys if key}

    async with active_threads_lock:
        binding = active_runs_by_thread.get(thread_id)
        if binding is None:
            return False
        run_task = binding.run_task
        if run_task is None or run_task.done():
            active_runs_by_thread.pop(thread_id, None)
            return False
        return owner_scope_matches(binding.owner_keys, candidate_keys)


async def _cancel_active_run_for_thread(
    thread_id: str | None,
    owner_keys: list[str],
    *,
    active_threads_lock: asyncio.Lock,
    active_runs_by_thread: dict[str, Any],
    owner_scope_matches: Callable[[set[str], set[str]], bool],
) -> bool:
    """Cancel one in-flight run for the current owner when present."""

    if not thread_id:
        return False
    candidate_keys = {key for key in owner_keys if key}

    run_task: asyncio.Task[Any] | None = None
    async with active_threads_lock:
        binding = active_runs_by_thread.get(thread_id)
        if binding is None:
            return False
        if not owner_scope_matches(binding.owner_keys, candidate_keys):
            return False
        run_task = binding.run_task
        if run_task is None or run_task.done():
            active_runs_by_thread.pop(thread_id, None)
            return False

    run_task.cancel()
    return True


async def _mark_thread_running(
    owner_keys: list[str],
    thread_id: str | None,
    *,
    active_threads_lock: asyncio.Lock,
    active_threads_by_owner: dict[str, set[str]],
) -> None:
    """Record a thread as actively running for one or more owners."""

    if not thread_id:
        return
    unique_keys = [key for key in dict.fromkeys(owner_keys) if key]
    if not unique_keys:
        return

    async with active_threads_lock:
        for owner_key in unique_keys:
            active = active_threads_by_owner.setdefault(owner_key, set())
            active.add(thread_id)


async def _mark_thread_stopped(
    owner_keys: list[str],
    thread_id: str | None,
    *,
    active_threads_lock: asyncio.Lock,
    active_threads_by_owner: dict[str, set[str]],
) -> None:
    """Remove one thread from active tracking for one or more owners."""

    if not thread_id:
        return
    unique_keys = [key for key in dict.fromkeys(owner_keys) if key]
    if not unique_keys:
        return

    async with active_threads_lock:
        for owner_key in unique_keys:
            active = active_threads_by_owner.get(owner_key)
            if not active:
                continue
            active.discard(thread_id)
            if not active:
                active_threads_by_owner.pop(owner_key, None)


async def _get_active_threads_for_owner_keys(
    owner_keys: list[str],
    *,
    active_threads_lock: asyncio.Lock,
    active_threads_by_owner: dict[str, set[str]],
) -> list[str]:
    """Return sorted active thread ids for current owner key set."""

    unique_keys = [key for key in dict.fromkeys(owner_keys) if key]
    if not unique_keys:
        return []

    async with active_threads_lock:
        active: set[str] = set()
        for owner_key in unique_keys:
            active.update(active_threads_by_owner.get(owner_key, set()))
        return sorted(active)


async def _register_active_run(
    thread_id: str | None,
    owner_keys: list[str],
    stream_session: Any | None,
    run_task: asyncio.Task[Any] | None,
    *,
    active_threads_lock: asyncio.Lock,
    active_runs_by_thread: dict[str, Any],
    active_run_binding_cls: Any,
) -> None:
    """Register one active run binding for reconnect-aware streaming."""

    if not thread_id:
        return
    unique_keys = {key for key in owner_keys if key}
    async with active_threads_lock:
        active_runs_by_thread[thread_id] = active_run_binding_cls(
            owner_keys=unique_keys,
            stream_session=stream_session,
            run_task=run_task,
        )


async def _unregister_active_run(
    thread_id: str | None,
    *,
    active_threads_lock: asyncio.Lock,
    active_runs_by_thread: dict[str, Any],
) -> None:
    """Remove one active run binding after completion."""

    if not thread_id:
        return
    async with active_threads_lock:
        active_runs_by_thread.pop(thread_id, None)


async def _rebind_active_run_session(
    thread_id: str | None,
    owner_keys: list[str],
    *,
    active_threads_lock: asyncio.Lock,
    active_runs_by_thread: dict[str, Any],
    owner_scope_matches: Callable[[set[str], set[str]], bool],
    get_current_chainlit_session_object: Callable[[], Any | None],
    logger: Any,
) -> None:
    """Rebind one active run's emitter session to the current websocket session."""

    if not thread_id:
        return
    candidate_keys = {key for key in owner_keys if key}
    current_session = get_current_chainlit_session_object()
    if current_session is None:
        return

    async with active_threads_lock:
        binding = active_runs_by_thread.get(thread_id)
        if binding is None:
            return
        if not owner_scope_matches(binding.owner_keys, candidate_keys):
            return
        source = binding.stream_session
        if source is None:
            return
        try:
            source.emit = current_session.emit
            source.emit_call = current_session.emit_call
            source.environ = current_session.environ
            # Keep stop/cancel targeting the original in-flight task after refresh.
            if binding.run_task is not None and not binding.run_task.done():
                current_session.current_task = binding.run_task
        except Exception as exc:
            logger.debug("Failed to rebind active run session for %s: %s", thread_id, exc)


async def _sync_running_threads_window_state(
    *,
    resolve_activity_owner_keys: Callable[[], list[str]],
    get_active_threads_for_owner_keys: Callable[[list[str]], Awaitable[list[str]]],
    get_current_thread_id: Callable[[], str | None],
    thread_has_active_run_for_owner: Callable[[str | None, list[str]], Awaitable[bool]],
    get_current_chainlit_session_object: Callable[[], Any | None],
    rebind_active_run_session: Callable[[str | None, list[str]], Awaitable[None]],
    send_window_message: Callable[[dict[str, str]], Awaitable[None]],
    logger: Any,
) -> None:
    """Re-send running-thread start signals after client reconnect/refresh."""

    owner_keys = resolve_activity_owner_keys()
    active_threads = await get_active_threads_for_owner_keys(owner_keys)
    current_thread_id = get_current_thread_id()

    # Owner-key matching can miss runs after refresh because websocket session ids
    # rotate. If the currently viewed thread still has an active run binding, keep
    # it in sync explicitly.
    if current_thread_id and current_thread_id not in active_threads:
        if await thread_has_active_run_for_owner(current_thread_id, owner_keys):
            active_threads = [current_thread_id, *active_threads]
    active_threads = list(dict.fromkeys(active_threads))
    if active_threads:
        current_session = get_current_chainlit_session_object()
        if current_session is not None:
            try:
                # Restore Chainlit's loading state so the native stop button reappears
                # after refresh while a run is still active.
                await current_session.emit("task_start", {})
                # Stop visibility also depends on first-interaction state in Chainlit.
                # Re-emit for the current thread to recover this state on refresh.
                if current_thread_id:
                    await current_session.emit(
                        "first_interaction",
                        {"interaction": "resume", "thread_id": current_thread_id},
                    )
            except Exception as exc:
                logger.debug(
                    "Failed to restore reconnect task state for active runs: %s",
                    exc,
                )
    for thread_id in active_threads:
        await rebind_active_run_session(thread_id, owner_keys)
        await send_window_message(
            {
                "type": "assistant_thinking",
                "status": "start",
                "thread_id": thread_id,
            }
        )
