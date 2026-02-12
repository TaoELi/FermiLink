from __future__ import annotations

import asyncio
from dataclasses import dataclass


class QueueFullError(RuntimeError):
    """Raised when the pending-run admission queue is full."""


@dataclass(slots=True)
class _QueuedRun:
    """One pending run request waiting for admission."""

    request_id: int
    user_key: str
    future: asyncio.Future


class RunAdmissionController:
    """In-memory admission queue for global/per-user run concurrency limits."""

    def __init__(
        self,
        *,
        global_limit: int,
        per_user_limit: int,
        max_queue_size: int = 0,
        max_pending_per_user: int = 0,
    ) -> None:
        self.global_limit = max(1, int(global_limit))
        self.per_user_limit = max(1, int(per_user_limit))
        self.max_queue_size = max(0, int(max_queue_size))
        self.max_pending_per_user = max(0, int(max_pending_per_user))
        self._lock = asyncio.Lock()
        self._active_total = 0
        self._active_by_user: dict[str, int] = {}
        self._pending_by_user: dict[str, int] = {}
        self._pending: list[_QueuedRun] = []
        self._next_request_id = 1

    @staticmethod
    def _normalize_user_key(user_key: str | None) -> str:
        """Normalize user keys used by queue counters."""

        cleaned = (user_key or "").strip().lower()
        return cleaned or "anonymous"

    def _can_run_locked(self, user_key: str) -> bool:
        """Check whether a user can start immediately under active counters."""

        if self._active_total >= self.global_limit:
            return False
        return self._active_by_user.get(user_key, 0) < self.per_user_limit

    def _grant_locked(
        self, *, user_key: str, request_id: int, queued: bool
    ) -> dict[str, int | bool]:
        """Reserve one active slot and return grant metadata."""

        self._active_total += 1
        per_user_active = self._active_by_user.get(user_key, 0) + 1
        self._active_by_user[user_key] = per_user_active
        return {
            "request_id": request_id,
            "queued": queued,
            "active_total": self._active_total,
            "global_limit": self.global_limit,
            "per_user_active": per_user_active,
            "per_user_limit": self.per_user_limit,
            "pending_total": len(self._pending),
        }

    def _increment_pending_user_locked(self, user_key: str) -> None:
        """Increment pending queue count for one user."""

        self._pending_by_user[user_key] = self._pending_by_user.get(user_key, 0) + 1

    def _decrement_pending_user_locked(self, user_key: str) -> None:
        """Decrement pending queue count for one user."""

        current = self._pending_by_user.get(user_key, 0)
        if current <= 1:
            self._pending_by_user.pop(user_key, None)
            return
        self._pending_by_user[user_key] = current - 1

    def _remove_pending_request_locked(self, request_id: int) -> bool:
        """Remove one queued request by id and update per-user counters."""

        for idx, item in enumerate(self._pending):
            if item.request_id != request_id:
                continue
            self._pending.pop(idx)
            self._decrement_pending_user_locked(item.user_key)
            return True
        return False

    def _dispatch_pending_locked(self) -> None:
        """Promote the first eligible queued runs while capacity exists."""

        while self._active_total < self.global_limit and self._pending:
            eligible_idx: int | None = None
            for idx, pending in enumerate(self._pending):
                if self._active_by_user.get(pending.user_key, 0) < self.per_user_limit:
                    eligible_idx = idx
                    break

            if eligible_idx is None:
                break

            pending = self._pending.pop(eligible_idx)
            self._decrement_pending_user_locked(pending.user_key)
            if pending.future.cancelled():
                continue

            grant = self._grant_locked(
                user_key=pending.user_key,
                request_id=pending.request_id,
                queued=True,
            )
            if not pending.future.done():
                pending.future.set_result(grant)

    async def acquire(self, user_key: str | None) -> dict[str, int | bool]:
        """Acquire admission for a run, queueing when limits are saturated."""

        normalized_user = self._normalize_user_key(user_key)
        pending: _QueuedRun | None = None

        async with self._lock:
            if self._can_run_locked(normalized_user) and not self._pending:
                request_id = self._next_request_id
                self._next_request_id += 1
                return self._grant_locked(
                    user_key=normalized_user,
                    request_id=request_id,
                    queued=False,
                )

            if (
                self.max_pending_per_user > 0
                and self._pending_by_user.get(normalized_user, 0)
                >= self.max_pending_per_user
            ):
                raise QueueFullError(
                    "Runner queue is full for this user "
                    f"({self.max_pending_per_user} pending requests)."
                )

            if self.max_queue_size > 0 and len(self._pending) >= self.max_queue_size:
                raise QueueFullError(
                    f"Runner queue is full ({self.max_queue_size} pending requests)."
                )

            request_id = self._next_request_id
            self._next_request_id += 1
            pending = _QueuedRun(
                request_id=request_id,
                user_key=normalized_user,
                future=asyncio.get_running_loop().create_future(),
            )
            self._pending.append(pending)
            self._increment_pending_user_locked(normalized_user)
            # Avoid head-of-line blocking when queue head is per-user limited.
            self._dispatch_pending_locked()
            if pending.future.done():
                return pending.future.result()

        try:
            return await pending.future
        except asyncio.CancelledError:
            async with self._lock:
                self._remove_pending_request_locked(pending.request_id)
                self._dispatch_pending_locked()
            raise

    async def release(self, user_key: str | None) -> None:
        """Release one active slot and admit queued work if possible."""

        normalized_user = self._normalize_user_key(user_key)
        async with self._lock:
            current = self._active_by_user.get(normalized_user, 0)
            if current <= 0:
                return

            if current == 1:
                self._active_by_user.pop(normalized_user, None)
            else:
                self._active_by_user[normalized_user] = current - 1

            if self._active_total > 0:
                self._active_total -= 1
            self._dispatch_pending_locked()

    async def snapshot(self) -> dict[str, int]:
        """Return current queue/accounting counters (for tests/diagnostics)."""

        async with self._lock:
            return {
                "active_total": self._active_total,
                "pending_total": len(self._pending),
                "global_limit": self.global_limit,
                "per_user_limit": self.per_user_limit,
            }

    async def snapshot_for_user(self, user_key: str | None) -> dict[str, int | bool]:
        """Return current queue/accounting counters for one normalized user key.

        Parameters
        ----------
        user_key : str or None
            User identifier to inspect.

        Returns
        -------
        dict[str, int | bool]
            Snapshot containing global/per-user counters and `can_run_now`.
        """

        normalized_user = self._normalize_user_key(user_key)
        async with self._lock:
            per_user_active = self._active_by_user.get(normalized_user, 0)
            per_user_pending = self._pending_by_user.get(normalized_user, 0)
            can_run_now = self._can_run_locked(normalized_user) and not self._pending
            return {
                "user_key": normalized_user,
                "can_run_now": can_run_now,
                "per_user_active": per_user_active,
                "per_user_pending": per_user_pending,
                "active_total": self._active_total,
                "pending_total": len(self._pending),
                "global_limit": self.global_limit,
                "per_user_limit": self.per_user_limit,
            }
