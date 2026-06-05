"""Document model — the core ledger of all ingested invoices / receipts.

Every uploaded document flows through the extraction pipeline and lands
here with its structured JSONB extraction, tax verdict, and verification
results. The ``(ca_firm_id, client_id)`` pair scopes every query.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Document(Base):
    """A processed invoice, receipt, or financial document."""

    __tablename__ = "documents"
    __table_args__ = (
        Index("idx_documents_firm_client", "ca_firm_id", "client_id"),
        Index(
            "idx_documents_filing",
            "ca_firm_id",
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
    document_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment=(
            "B2B_INVOICE | B2C_INVOICE | FREIGHT | RESTAURANT | "
            "IMPORT | PROFESSIONAL | CAPITAL_GOODS"
        ),
    )
    source_file_ids: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="Telegram file_id array.",
    )
    extraction_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="Full structured extraction output.",
    )
    tax_verdict: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Five optimisation variables: claim, defer, block, rcm, tds.",
    )
    verification_result: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Red-team deterministic verification output.",
    )
    filing_period_month: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    filing_period_year: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    processing_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'pending'"),
        comment="pending | processing | completed | failed",
    )
    processing_time_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


__all__ = ["Document"]
