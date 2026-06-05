"""Tests for the BackgroundTaskRegistry."""

from __future__ import annotations

import asyncio

import pytest
from app.utils.background import BackgroundTaskRegistry


@pytest.mark.asyncio
async def test_spawn_and_complete_clears_registry() -> None:
    reg = BackgroundTaskRegistry()
    completed: list[int] = []

    async def work() -> None:
        completed.append(1)

    reg.spawn(work(), name="work")
    assert reg.in_flight() == 1
    # Drain quickly — the task finishes within milliseconds.
    await reg.drain(timeout=2.0)
    assert reg.in_flight() == 0
    assert completed == [1]


@pytest.mark.asyncio
async def test_failure_does_not_propagate() -> None:
    reg = BackgroundTaskRegistry()

    async def boom() -> None:
        raise RuntimeError("explode")

    # If the wrapper failed to catch, this line would raise.
    reg.spawn(boom(), name="boom", context={"chat_id": 7})
    await reg.drain(timeout=2.0)
    assert reg.in_flight() == 0


@pytest.mark.asyncio
async def test_drain_cancels_pending_tasks_after_timeout() -> None:
    reg = BackgroundTaskRegistry()
    started = asyncio.Event()

    async def hang() -> None:
        started.set()
        # Sleep longer than our drain timeout so we exercise the cancel path.
        await asyncio.sleep(5)

    reg.spawn(hang(), name="hang")
    await started.wait()
    await reg.drain(timeout=0.05)
    assert reg.in_flight() == 0


@pytest.mark.asyncio
async def test_drain_with_no_tasks_is_noop() -> None:
    reg = BackgroundTaskRegistry()
    await reg.drain(timeout=1.0)
    assert reg.in_flight() == 0


@pytest.mark.asyncio
async def test_multiple_tasks_are_tracked() -> None:
    reg = BackgroundTaskRegistry()
    counter = [0]

    async def inc() -> None:
        counter[0] += 1

    for _ in range(5):
        reg.spawn(inc(), name="inc")
    assert reg.in_flight() == 5
    await reg.drain(timeout=2.0)
    assert counter[0] == 5
