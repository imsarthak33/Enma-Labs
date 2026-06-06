"""Task CRUD + due-task queries — all scoped to ``ca_firm_id``.

Phase 6 adds the basic supervisor surface: create, list, mark complete.
Phase 7 layers the heartbeat / overdue logic on top.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Final, cast

from app.db.models.task import Task
from app.db.queries.base import BaseQuery

__all__ = ["HEARTBEAT_COOLDOWN_MINUTES", "TaskQuery"]


HEARTBEAT_COOLDOWN_MINUTES: Final[int] = 30
"""Per-task quiet window between notifications — ADR-007 §Decision 4."""


class TaskQuery(BaseQuery):
    """Tenant-scoped task operations."""

    async def create(
        self,
        *,
        title: str,
        description: str | None = None,
        client_id: uuid.UUID | str | None = None,
        due_at: datetime | None = None,
        priority: int = 0,
    ) -> Task:
        """Insert a new task for this firm."""
        cid: uuid.UUID | None
        if client_id is None:
            cid = None
        elif isinstance(client_id, uuid.UUID):
            cid = client_id
        else:
            cid = uuid.UUID(str(client_id))
        task = Task(
            client_id=cid,
            title=title,
            description=description,
            due_at=due_at,
            priority=priority,
        )
        return cast(Task, await self._insert(task))

    async def list_open(
        self, *, client_id: uuid.UUID | str | None = None
    ) -> Sequence[Task]:
        """Return all pending or in-progress tasks for this firm.

        Optionally narrow to a single client.
        """
        stmt = (
            self._scoped_select(Task)
            .where(Task.status.in_(("pending", "in_progress")))
            .order_by(
                Task.priority.desc(),
                Task.due_at.asc().nulls_last(),
                Task.created_at.asc(),
            )
        )
        if client_id is not None:
            cid = (
                client_id
                if isinstance(client_id, uuid.UUID)
                else uuid.UUID(str(client_id))
            )
            stmt = stmt.where(Task.client_id == cid)
        return await self._fetch_all(stmt)

    async def list_overdue(self) -> Sequence[Task]:
        """Return all pending tasks past their due date for this firm."""
        now = datetime.now(UTC)
        stmt = (
            self._scoped_select(Task)
            .where(Task.status == "pending")
            .where(Task.due_at.is_not(None))
            .where(Task.due_at <= now)
            .order_by(Task.due_at.asc())
        )
        return await self._fetch_all(stmt)

    async def get_by_id(self, task_id: uuid.UUID | str) -> Task | None:
        tid = task_id if isinstance(task_id, uuid.UUID) else uuid.UUID(str(task_id))
        stmt = self._scoped_select(Task).where(Task.id == tid)
        return await self._fetch_one(stmt)

    async def mark_complete(self, task_id: uuid.UUID | str) -> Task | None:
        return await self._update(Task, task_id, status="completed")

    async def update_status(
        self, task_id: uuid.UUID | str, *, status: str
    ) -> Task | None:
        if status not in ("pending", "in_progress", "completed", "cancelled"):
            raise ValueError(f"unknown task status: {status!r}")
        return await self._update(Task, task_id, status=status)

    # ----- Phase 7: heartbeat surface --------------------------------------

    async def list_due_for_heartbeat(
        self, *, cooldown_minutes: int = HEARTBEAT_COOLDOWN_MINUTES
    ) -> Sequence[Task]:
        """Return overdue pending tasks whose cooldown has expired.

        Selection rule:
          * ``status = 'pending'``
          * ``due_at <= NOW()``
          * ``last_notified_at IS NULL`` OR older than ``cooldown_minutes`` ago.
        """
        now = datetime.now(UTC)
        cooldown_floor = now - timedelta(minutes=cooldown_minutes)
        from sqlalchemy import or_

        stmt = (
            self._scoped_select(Task)
            .where(Task.status == "pending")
            .where(Task.due_at.is_not(None))
            .where(Task.due_at <= now)
            .where(
                or_(
                    Task.last_notified_at.is_(None),
                    Task.last_notified_at < cooldown_floor,
                )
            )
            .order_by(Task.due_at.asc())
        )
        return await self._fetch_all(stmt)

    async def mark_notified(self, task_id: uuid.UUID | str) -> Task | None:
        """Stamp ``last_notified_at = NOW()`` after a Telegram send.

        Caller must invoke this AFTER a successful Telegram ack; calling
        before would silence the task if the Telegram call fails.
        """
        return await self._update(Task, task_id, last_notified_at=datetime.now(UTC))
