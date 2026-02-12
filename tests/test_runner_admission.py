from __future__ import annotations

import asyncio

import pytest

from fermilink.runner.admission import QueueFullError, RunAdmissionController


def test_acquire_and_release_updates_active_counts() -> None:
    async def scenario() -> None:
        controller = RunAdmissionController(
            global_limit=2, per_user_limit=1, max_queue_size=10
        )
        grant = await controller.acquire("alice")
        assert grant["queued"] is False

        snapshot = await controller.snapshot()
        assert snapshot["active_total"] == 1
        assert snapshot["pending_total"] == 0

        await controller.release("alice")
        snapshot = await controller.snapshot()
        assert snapshot["active_total"] == 0
        assert snapshot["pending_total"] == 0

    asyncio.run(scenario())


def test_global_limit_queues_until_slot_is_released() -> None:
    async def scenario() -> None:
        controller = RunAdmissionController(
            global_limit=1, per_user_limit=1, max_queue_size=10
        )
        await controller.acquire("alice")

        queued_task = asyncio.create_task(controller.acquire("bob"))
        await asyncio.sleep(0.05)
        assert not queued_task.done()

        await controller.release("alice")
        queued_grant = await asyncio.wait_for(queued_task, timeout=0.5)
        assert queued_grant["queued"] is True

        snapshot = await controller.snapshot()
        assert snapshot["active_total"] == 1
        assert snapshot["pending_total"] == 0

        await controller.release("bob")

    asyncio.run(scenario())


def test_per_user_limit_does_not_block_other_users() -> None:
    async def scenario() -> None:
        controller = RunAdmissionController(
            global_limit=2, per_user_limit=1, max_queue_size=10
        )
        await controller.acquire("alice")

        same_user_task = asyncio.create_task(controller.acquire("alice"))
        await asyncio.sleep(0.05)
        assert not same_user_task.done()

        other_user_grant = await asyncio.wait_for(controller.acquire("bob"), timeout=0.5)
        assert other_user_grant["per_user_active"] == 1

        await controller.release("alice")
        queued_grant = await asyncio.wait_for(same_user_task, timeout=0.5)
        assert queued_grant["queued"] is True

        await controller.release("alice")
        await controller.release("bob")
        snapshot = await controller.snapshot()
        assert snapshot["active_total"] == 0
        assert snapshot["pending_total"] == 0

    asyncio.run(scenario())


def test_queue_full_raises_error() -> None:
    async def scenario() -> None:
        controller = RunAdmissionController(
            global_limit=1, per_user_limit=1, max_queue_size=1
        )
        await controller.acquire("alice")

        queued_task = asyncio.create_task(controller.acquire("bob"))
        await asyncio.sleep(0.05)
        with pytest.raises(QueueFullError):
            await controller.acquire("charlie")

        await controller.release("alice")
        await asyncio.wait_for(queued_task, timeout=0.5)
        await controller.release("bob")

    asyncio.run(scenario())


def test_cancelled_waiter_is_removed_from_queue() -> None:
    async def scenario() -> None:
        controller = RunAdmissionController(
            global_limit=1, per_user_limit=1, max_queue_size=10
        )
        await controller.acquire("alice")

        queued_task = asyncio.create_task(controller.acquire("bob"))
        await asyncio.sleep(0.05)
        queued_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued_task

        snapshot = await controller.snapshot()
        assert snapshot["pending_total"] == 0

        await controller.release("alice")
        grant = await asyncio.wait_for(controller.acquire("bob"), timeout=0.5)
        assert grant["queued"] is False
        await controller.release("bob")

    asyncio.run(scenario())


def test_per_user_pending_cap_blocks_spammer() -> None:
    async def scenario() -> None:
        controller = RunAdmissionController(
            global_limit=1,
            per_user_limit=1,
            max_queue_size=10,
            max_pending_per_user=2,
        )
        await controller.acquire("alice")

        queued1 = asyncio.create_task(controller.acquire("alice"))
        queued2 = asyncio.create_task(controller.acquire("alice"))
        await asyncio.sleep(0.05)

        with pytest.raises(QueueFullError):
            await controller.acquire("alice")

        await controller.release("alice")
        await asyncio.wait_for(queued1, timeout=0.5)
        await controller.release("alice")
        await asyncio.wait_for(queued2, timeout=0.5)
        await controller.release("alice")

        snapshot = await controller.snapshot()
        assert snapshot["active_total"] == 0
        assert snapshot["pending_total"] == 0

    asyncio.run(scenario())


def test_other_user_can_queue_when_spammer_hits_pending_cap() -> None:
    async def scenario() -> None:
        controller = RunAdmissionController(
            global_limit=1,
            per_user_limit=1,
            max_queue_size=3,
            max_pending_per_user=2,
        )
        await controller.acquire("alice")

        queued1 = asyncio.create_task(controller.acquire("alice"))
        queued2 = asyncio.create_task(controller.acquire("alice"))
        await asyncio.sleep(0.05)

        with pytest.raises(QueueFullError):
            await controller.acquire("alice")

        bob_task = asyncio.create_task(controller.acquire("bob"))
        await asyncio.sleep(0.05)
        assert not bob_task.done()

        snapshot = await controller.snapshot()
        assert snapshot["pending_total"] == 3

        await controller.release("alice")
        await asyncio.wait_for(queued1, timeout=0.5)
        await controller.release("alice")
        await asyncio.wait_for(queued2, timeout=0.5)
        await controller.release("alice")

        bob_grant = await asyncio.wait_for(bob_task, timeout=0.5)
        assert bob_grant["queued"] is True
        await controller.release("bob")

        snapshot = await controller.snapshot()
        assert snapshot["active_total"] == 0
        assert snapshot["pending_total"] == 0

    asyncio.run(scenario())


def test_snapshot_for_user_reports_readiness_and_per_user_counts() -> None:
    async def scenario() -> None:
        controller = RunAdmissionController(
            global_limit=2, per_user_limit=1, max_queue_size=10
        )
        await controller.acquire("alice")
        queued_alice = asyncio.create_task(controller.acquire("alice"))
        await asyncio.sleep(0.05)

        alice = await controller.snapshot_for_user("alice")
        assert alice["user_key"] == "alice"
        assert alice["per_user_active"] == 1
        assert alice["per_user_pending"] == 1
        assert alice["can_run_now"] is False

        bob = await controller.snapshot_for_user("bob")
        assert bob["user_key"] == "bob"
        assert bob["per_user_active"] == 0
        assert bob["per_user_pending"] == 0
        assert bob["can_run_now"] is False

        await controller.release("alice")
        await asyncio.wait_for(queued_alice, timeout=0.5)
        await controller.release("alice")

    asyncio.run(scenario())
