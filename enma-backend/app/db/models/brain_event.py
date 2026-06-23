"""Brain-event model — one normalised fact from a source system.

The substrate of Layer A's Brain Ingestion (P1). Every adapter — Tally,
Gmail, WhatsApp groups, GSTN portal — translates its domain into rows
of this single table, so the human CA and the agents read one source of
truth (the "company brain").

Append-only and materialised as a TimescaleDB hypertable on
``occurred_at`` (see migration 013). The composite PK
``(occurred_at, id)`` is what the hypertable requires. ``occurred_at``
is the event's time in the *source* system; ``ingested_at`` is when
Enma recorded it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BrainEvent(Base):
    """One observed fact from a source system, normalised + firm-scoped."""

    __tablename__ = "brain_events"
    __table_args__ = (
        Index("ix_brain_events_firm_time", "ca_firm_id", "occurred_at"),
        Index(
            "ix_brain_events_firm_source_type",
            "ca_firm_id",
            "source",
            "event_type",
            "occurred_at",
        ),
        # Partial unique — idempotent re-ingest. Mirrors migration 013's
        # uq_brain_events_dedup; occurred_at is in the tuple so the index
        # stays valid once the table becomes a Timescale hypertable.
        Index(
            "uq_brain_events_dedup",
            "ca_firm_id",
            "source",
            "dedup_key",
            "occurred_at",
            unique=True,
            postgresql_where=text("dedup_key IS NOT NULL"),
        ),
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
        primary_key=True,
        comment="When the event happened in the SOURCE system.",
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
        primary_key=True,
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
        comment="When Enma recorded the event.",
    )
    ca_firm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ca_firms.id", ondelete="CASCADE"),
        nullable=False,
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="SET NULL"),
        nullable=True,
    )
    source: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        comment="tally | gmail | whatsapp_group | gstn_portal | enma_internal",
    )
    event_type: Mapped[str] = mapped_column(
        String(60),
        nullable=False,
        comment="Adapter-defined event name (e.g. 'voucher_seen', 'email_received').",
    )
    dedup_key: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Source-stable identifier (Gmail Message-ID, Tally voucher GUID, "
            "GSTN ARN, …). NULL for events with no natural key (never deduped)."
        ),
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="Normalised event body.",
    )


__all__ = ["BrainEvent"]
