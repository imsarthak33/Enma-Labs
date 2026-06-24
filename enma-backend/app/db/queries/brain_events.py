"""Brain-event CRUD — the Brain-layer ingestion landing zone, firm-scoped.

Append-only. Two write paths:

* :meth:`record` — plain insert. Use for internal events that carry no
  natural dedup key (``source='enma_internal'``) or when the caller has
  already guaranteed uniqueness.
* :meth:`record_dedup` — idempotent insert keyed on
  ``(ca_firm_id, source, dedup_key, occurred_at)`` via
  ``ON CONFLICT DO NOTHING``. Use from every re-polling adapter (Tally,
  Gmail, GSTN) so re-presenting the same source fact is a no-op. Returns
  the inserted row, or ``None`` when the row already existed.

Reads are the P3 Brain-Surface's job; the handful here cover the
adapter-side "what have I already seen" checks A2/A3 need.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models.brain_event import BrainEvent
from app.db.queries.base import BaseQuery

# Mirrors migration 013's ck_brain_events_source CHECK constraint. Kept
# here so a typo'd source fails fast in Python with a clear message
# instead of surfacing as a Postgres CHECK violation mid-transaction.
_ALLOWED_SOURCES: frozenset[str] = frozenset(
    {"tally", "gmail", "whatsapp_group", "gstn_portal", "enma_internal", "bank"}
)


class BrainEventQuery(BaseQuery):
    """Tenant-scoped brain-event operations."""

    @staticmethod
    def _coerce_client_id(
        client_id: uuid.UUID | str | None,
    ) -> uuid.UUID | None:
        if client_id is None or isinstance(client_id, uuid.UUID):
            return client_id
        return uuid.UUID(str(client_id))

    @staticmethod
    def _check_source(source: str) -> None:
        if source not in _ALLOWED_SOURCES:
            raise ValueError(
                f"unknown brain_event source {source!r}; "
                f"allowed: {sorted(_ALLOWED_SOURCES)}"
            )

    async def record(
        self,
        *,
        source: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
        client_id: uuid.UUID | str | None = None,
        dedup_key: str | None = None,
    ) -> BrainEvent:
        """Append one event unconditionally. ``occurred_at`` defaults to NOW().

        Does not dedup — a duplicate ``dedup_key`` will raise an
        ``IntegrityError`` from the partial unique index. Use
        :meth:`record_dedup` when re-ingest is possible.
        """
        self._check_source(source)
        row = BrainEvent(
            source=source,
            event_type=event_type,
            payload=payload or {},
            client_id=self._coerce_client_id(client_id),
            dedup_key=dedup_key,
        )
        if occurred_at is not None:
            row.occurred_at = occurred_at
        return cast(BrainEvent, await self._insert(row))

    async def record_dedup(
        self,
        *,
        source: str,
        event_type: str,
        dedup_key: str,
        occurred_at: datetime,
        payload: dict[str, Any] | None = None,
        client_id: uuid.UUID | str | None = None,
    ) -> BrainEvent | None:
        """Idempotent insert. Returns the new row, or ``None`` if a dup.

        Keyed on ``(ca_firm_id, source, dedup_key, occurred_at)`` to match
        the ``uq_brain_events_dedup`` partial unique index. ``occurred_at``
        is required (and part of the key) because a source fact has a fixed
        time — re-polling presents the same ``occurred_at``, so the conflict
        target catches the repeat.
        """
        self._check_source(source)
        stmt = (
            pg_insert(BrainEvent)
            .values(
                ca_firm_id=self.ca_firm_id,
                source=source,
                event_type=event_type,
                dedup_key=dedup_key,
                occurred_at=occurred_at,
                payload=payload or {},
                client_id=self._coerce_client_id(client_id),
            )
            .on_conflict_do_nothing(
                index_elements=["ca_firm_id", "source", "dedup_key", "occurred_at"],
                index_where=sa_text("dedup_key IS NOT NULL"),
            )
            .returning(BrainEvent)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one_or_none()

    async def record_dedup_bulk(
        self, *, rows: list[dict[str, Any]]
    ) -> tuple[int, int]:
        """Idempotent bulk insert. Returns ``(inserted, skipped)``.

        Built for a Tally Day Book import — hundreds to thousands of
        vouchers in one upload — so it issues a single
        ``INSERT ... ON CONFLICT DO NOTHING RETURNING`` instead of N
        round-trips. ``skipped`` counts everything not newly inserted:
        rows already in the DB *and* intra-file duplicates.

        Each row dict needs ``source``, ``event_type``, ``dedup_key``,
        ``occurred_at`` (tz-aware), and optionally ``payload`` /
        ``client_id``. ``ca_firm_id`` is injected from the query scope —
        callers never pass it (tenant isolation by construction).

        Intra-batch duplicates are collapsed in Python first: ON CONFLICT
        DO NOTHING resolves conflicts against *existing* rows, not against
        two identical rows inside the same statement, so we de-dupe on
        ``(dedup_key, occurred_at)`` before the insert.
        """
        original_total = len(rows)
        if original_total == 0:
            return (0, 0)

        seen: set[tuple[str, datetime]] = set()
        values: list[dict[str, Any]] = []
        for r in rows:
            dedup_key = r["dedup_key"]
            occurred_at = r["occurred_at"]
            key = (dedup_key, occurred_at)
            if key in seen:
                continue
            seen.add(key)
            self._check_source(r["source"])
            values.append(
                {
                    "ca_firm_id": self.ca_firm_id,
                    "source": r["source"],
                    "event_type": r["event_type"],
                    "dedup_key": dedup_key,
                    "occurred_at": occurred_at,
                    "payload": r.get("payload") or {},
                    "client_id": self._coerce_client_id(r.get("client_id")),
                }
            )

        stmt = (
            pg_insert(BrainEvent)
            .values(values)
            .on_conflict_do_nothing(
                index_elements=["ca_firm_id", "source", "dedup_key", "occurred_at"],
                index_where=sa_text("dedup_key IS NOT NULL"),
            )
            .returning(BrainEvent.id)
        )
        result = await self.session.execute(stmt)
        inserted = len(result.fetchall())
        await self.session.flush()
        return (inserted, original_total - inserted)

    async def list_recent(
        self, *, limit: int = 50
    ) -> Sequence[BrainEvent]:
        """Most-recent events for this firm (by source time)."""
        stmt = (
            self._scoped_select(BrainEvent)
            .order_by(BrainEvent.occurred_at.desc())
            .limit(limit)
        )
        return await self._fetch_all(stmt)

    async def list_filtered(
        self,
        *,
        client_id: uuid.UUID | str | None = None,
        source: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> Sequence[BrainEvent]:
        """A5 Brain-Surface read: events for this firm, optionally narrowed.

        Powers the ``query_brain`` supervisor tool. Any combination of
        ``client_id`` / ``source`` / ``event_type`` may be supplied;
        omitting all returns the firm's most-recent events. Always
        firm-scoped via :meth:`_scoped_select`. Ordered newest-first by
        the source event time.
        """
        stmt = self._scoped_select(BrainEvent)
        if client_id is not None:
            stmt = stmt.where(BrainEvent.client_id == self._coerce_client_id(client_id))
        if source is not None:
            self._check_source(source)
            stmt = stmt.where(BrainEvent.source == source)
        if event_type is not None:
            stmt = stmt.where(BrainEvent.event_type == event_type)
        stmt = stmt.order_by(BrainEvent.occurred_at.desc()).limit(limit)
        return await self._fetch_all(stmt)

    async def list_by_source(
        self, *, source: str, limit: int = 50
    ) -> Sequence[BrainEvent]:
        """Most-recent events from one source (firm-scoped)."""
        self._check_source(source)
        stmt = (
            self._scoped_select(BrainEvent)
            .where(BrainEvent.source == source)
            .order_by(BrainEvent.occurred_at.desc())
            .limit(limit)
        )
        return await self._fetch_all(stmt)

    async def list_for_client(
        self, *, client_id: uuid.UUID | str, limit: int = 50
    ) -> Sequence[BrainEvent]:
        """Most-recent events tied to one client (firm-scoped)."""
        cid = self._coerce_client_id(client_id)
        stmt = (
            self._scoped_select(BrainEvent)
            .where(BrainEvent.client_id == cid)
            .order_by(BrainEvent.occurred_at.desc())
            .limit(limit)
        )
        return await self._fetch_all(stmt)


__all__ = ["BrainEventQuery"]
