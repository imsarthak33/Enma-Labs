"""Outcome-unit model — every billable artefact Enma produces.

One row per outcome (ITC recovered, filing prepared, notice drafted,
invoice processed, …). ``kind`` is the enum; ``quantity`` is a
``Decimal`` (DECIMAL(18,4)) for money-shaped kinds and a count for
unit-shaped kinds. ``metadata`` carries kind-specific detail without
schema churn.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OutcomeUnit(Base):
    """One billable outcome scoped to firm (+ optional client + document)."""

    __tablename__ = "outcome_units"
    __table_args__ = (
        Index(
            "ix_outcome_units_firm_kind_time",
            "ca_firm_id",
            "kind",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
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
    kind: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        comment=(
            "itc_recovered_inr | filed_return | drafted_notice | "
            "reconciled_period | invoice_processed | liaison_message_sent | "
            "tally_export_generated | client_ledger_exported"
        ),
    )
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
        comment="Rupee amount for money kinds; count (1) for unit kinds.",
    )
    confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4),
        nullable=True,
        comment="0.0000 to 1.0000. NULL when not applicable.",
    )
    ca_approval_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'pending'"),
        comment="pending | approved | rejected | auto_approved",
    )
    related_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="Kind-specific detail (period, scheme, etc.).",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


__all__ = ["OutcomeUnit"]
