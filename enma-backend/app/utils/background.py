"""Lightweight background-task registry.

We deliberately do NOT use ``fastapi.BackgroundTasks``:

  * They run *after* the response is sent, so they can't influence status.
  * They are not drained on lifespan shutdown — Uvicorn can SIGTERM them mid-flight.
  * They have no observability hook for "in-flight task count".

This module provides a process-scoped registry of ``asyncio.Task`` objects
with three operations:

  * :meth:`BackgroundTaskRegistry.spawn` — schedule a coroutine and remember it.
  * :meth:`BackgroundTaskRegistry.drain` — wait (with timeout) for all in-flight tasks.
  * :meth:`BackgroundTaskRegistry.in_flight` — current count, for metrics.

Failures inside background tasks are logged with structured context and
captured by Sentry (if configured). They never surface to the HTTP response —
by design, since the response has already been sent by the time they run.

Phase 3 only schedules a logging stub. Phase 4 swaps in the real extraction
pipeline. The registry API stays the same.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

import sentry_sdk

from app.logging_setup import get_logger

_log = get_logger(__name__)


class BackgroundTaskRegistry:
    """A set of in-flight asyncio tasks with structured lifecycle hooks."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    def spawn(
        self,
        coro: Coroutine[Any, Any, Any],
        *,
        name: str,
        context: dict[str, Any] | None = None,
    ) -> asyncio.Task[Any]:
        """Schedule ``coro`` and register the resulting Task.

        ``name`` is used for logging and ``asyncio.Task.get_name``; ``context``
        is attached to any failure log so a backgrounded error can be tied
        back to the request that spawned it.
        """
        task = asyncio.create_task(self._wrap(coro, name=name, context=context or {}), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return task

    async def _wrap(
        self,
        coro: Coroutine[Any, Any, Any],
        *,
        name: str,
        context: dict[str, Any],
    ) -> None:
        """Run ``coro`` and capture failures without re-raising into the loop."""
        try:
            await coro
        except asyncio.CancelledError:
            # Cancellation during drain is expected; let it propagate so the
            # task is marked cancelled rather than failed.
            raise
        except Exception as exc:  # terminal capture point — never re-raise
            _log.error(
                "background_task_failed",
                task=name,
                error=str(exc),
                exc_info=exc,
                **context,
            )
            sentry_sdk.capture_exception(exc)

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)

    def in_flight(self) -> int:
        return len(self._tasks)

    async def drain(self, timeout: float = 30.0) -> None:
        """Wait up to ``timeout`` seconds for in-flight tasks to finish.

        Tasks still running after the timeout are cancelled and given a brief
        grace period to clean up.
        """
        if not self._tasks:
            return
        in_flight = list(self._tasks)
        _log.info("background_drain_start", in_flight=len(in_flight), timeout_s=timeout)
        done, pending = await asyncio.wait(in_flight, timeout=timeout)
        if pending:
            _log.warning(
                "background_drain_timeout_cancelling",
                cancelled=len(pending),
                completed=len(done),
            )
            for t in pending:
                t.cancel()
            # Give the cancelled tasks a moment to unwind.
            await asyncio.gather(*pending, return_exceptions=True)
        _log.info("background_drain_complete")


_registry: BackgroundTaskRegistry | None = None


def get_registry() -> BackgroundTaskRegistry:
    """Return the process-wide registry, creating it on first call."""
    global _registry  # noqa: PLW0603 — intentional process-singleton
    if _registry is None:
        _registry = BackgroundTaskRegistry()
    return _registry


async def shutdown_registry(timeout: float = 30.0) -> None:
    """Drain and clear the process-wide registry. Called from lifespan exit."""
    global _registry  # noqa: PLW0603 — intentional process-singleton
    if _registry is not None:
        await _registry.drain(timeout=timeout)
    _registry = None


__all__ = ["BackgroundTaskRegistry", "get_registry", "shutdown_registry"]
