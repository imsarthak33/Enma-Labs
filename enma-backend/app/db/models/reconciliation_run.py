"""Reconciliation-run model — immutable audit of one ITC recon (ADR-015).

One row per (client, period) reconciliation a CA runs. Carries the
per-bucket counts and the recoverable / at-risk ITC totals. Mirrors
``tally_export_run`` — an append-only audit record, firm + client scoped.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReconciliationRun(Base):
    """One immutable ITC reconciliation result for a (client, period)."""

    __tablename__ = "reconciliation_runs"
    __table_args__ = (
        Index(
            "ix_reconciliation_runs_firm_client_period",
            "ca_firm_id",
            "client_id",
            "filing_period_year",
            "filing_period_month",
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
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    filing_period_month: Mapped[int] = mapped_column(Integer, nullable=False)
    filing_period_year: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'invoice_vs_2b'"),
        comment="invoice_vs_2b (MVP) | tri_way (phase 2 with bank leg)",
    )
    matched_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    amount_mismatch_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    in_books_not_in_2b_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    in_2b_not_in_books_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    recoverable_itc: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, server_default=text("0")
    )
    at_risk_itc: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, server_default=text("0")
    )
    report_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_by_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


__all__ = ["ReconciliationRun"]
