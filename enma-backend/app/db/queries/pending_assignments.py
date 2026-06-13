"""Pending-assignment CRUD — stage 5 of identity resolution.

All operations are tenant-scoped via :class:`BaseQuery`. Callers should
treat the table as transient state:

* The identity resolver creates one row per ambiguous document.
* The callback handler resolves it by attaching ``resolved_client_id``.
* The Phase 7 cleanup cron deletes rows older than 24h.

A pending row is considered *open* iff ``resolved_at IS NULL`` AND
``expires_at > NOW()``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast

from sqlalchemy import update

from app.db.models.pending_assignment import PendingAssignment
from app.db.queries.base import BaseQuery

__all__ = ["PENDING_ASSIGNMENT_TTL_SECONDS", "PendingAssignmentQuery"]


# Public so tests + identity resolver can reference the same value.
PENDING_ASSIGNMENT_TTL_SECONDS: Final[int] = 24 * 60 * 60  # 24 hours


class PendingAssignmentQuery(BaseQuery):
    """Tenant-scoped pending-assignment operations."""

    async def create(
        self,
        *,
        chat_id: int,
        file_ids: Sequence[str],
        candidate_client_ids: Sequence[uuid.UUID] | None = None,
        message_id: int | None = None,
        ttl_seconds: int = PENDING_ASSIGNMENT_TTL_SECONDS,
        extraction: dict[str, Any] | None = None,
        extraction_document_type: str | None = None,
    ) -> PendingAssignment:
        """Insert a new pending-assignment row for this firm.

        R2 — ``extraction`` and ``extraction_document_type`` cache the
        :func:`app.agents.pipeline.extract_only` output so a confirmation
        callback can call :func:`finalize_document` directly. Leave both
        ``None`` for legacy paths that haven't extracted yet.
        """
        now = datetime.now(UTC)
        candidate_ids: list[str] = [
            str(cid) for cid in (candidate_client_ids or ())
        ]
        row = PendingAssignment(
            chat_id=chat_id,
            message_id=message_id,
            file_ids=list(file_ids),
            candidate_client_ids=candidate_ids,
            expires_at=now + timedelta(seconds=ttl_seconds),
            extraction=extraction,
            extraction_document_type=extraction_document_type,
        )
        return cast(PendingAssignment, await self._insert(row))

    async def get_open_by_id(
        self, pending_id: uuid.UUID | str
    ) -> PendingAssignment | None:
        """Return the open pending row with this id, or ``None``.

        A row is *open* iff it has not been resolved AND has not expired.
        """
        pid = (
            pending_id
            if isinstance(pending_id, uuid.UUID)
            else uuid.UUID(str(pending_id))
        )
        stmt = self._scoped_select(PendingAssignment).where(
            PendingAssignment.id == pid
        )
        row = await self._fetch_one(stmt)
        if row is None or row.resolved_at is not None:
            return None
        if row.expires_at <= datetime.now(UTC):
            return None
        return row

    async def list_open_for_chat(self, chat_id: int) -> Sequence[PendingAssignment]:
        """Return all open pending rows for a (firm, chat)."""
        now = datetime.now(UTC)
        stmt = (
            self._scoped_select(PendingAssignment)
            .where(PendingAssignment.chat_id == chat_id)
            .where(PendingAssignment.resolved_at.is_(None))
            .where(PendingAssignment.expires_at > now)
            .order_by(PendingAssignment.created_at.desc())
        )
        return await self._fetch_all(stmt)

    async def attach_prompt_message(
        self,
        pending_id: uuid.UUID,
        prompt_message_id: int,
    ) -> PendingAssignment | None:
        """Record the message_id of the 'which client?' prompt."""
        return await self._update(
            PendingAssignment,
            pending_id,
            prompt_message_id=prompt_message_id,
        )

    async def resolve(
        self,
        pending_id: uuid.UUID,
        *,
        client_id: uuid.UUID,
    ) -> PendingAssignment | None:
        """Atomically set ``resolved_client_id`` + ``resolved_at``.

        The update is conditional on the row still being open — if the
        row was already resolved or has expired, returns ``None``.
        """
        now = datetime.now(UTC)
        stmt = (
            update(PendingAssignment)
            .where(PendingAssignment.id == pending_id)
            .where(PendingAssignment.ca_firm_id == self.ca_firm_id)
            .where(PendingAssignment.resolved_at.is_(None))
            .where(PendingAssignment.expires_at > now)
            .values(resolved_client_id=client_id, resolved_at=now)
            .returning(PendingAssignment)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one_or_none()
