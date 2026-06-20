"""Verdict-correction model — the LoRA training corpus.

Every time a CA tells Enma "actually that invoice should be classified
as RCM, not ITC-eligible", a row lands here. Invoice features are
anonymized before insert (single-stage write); ``anonymized_at`` is
recorded for audit but the row never holds un-anonymized payload.

Source document FK is ``ON DELETE SET NULL`` so the corpus survives a
``documents`` delete — losing a correction would lose training signal
we cannot recover.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CHAR, BigInteger, DateTime, ForeignKey, Index, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VerdictCorrection(Base):
    """A single CA correction of one document's tax verdict."""

    __tablename__ = "verdict_corrections"
    __table_args__ = (
        Index(
            "ix_verdict_corrections_firm_time",
            "ca_firm_id",
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
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    original_verdict: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="The tax_verdict JSONB the engine produced.",
    )
    corrected_verdict: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="The verdict shape the CA said is correct.",
    )
    correction_reason_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="CA's free-form explanation, verbatim.",
    )
    corrected_by_chat_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Telegram/WA chat id of the correcting CA.",
    )
    track: Mapped[str] = mapped_column(
        CHAR(1),
        nullable=False,
        server_default=text("'A'"),
        comment="'A' = SaaS firm; 'B' = Enma's own ICAI practice.",
    )
    confidence_self_assessed: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4),
        nullable=True,
        comment="CA's self-rating of how sure they are (0.0000 to 1.0000).",
    )
    invoice_features_anonymized: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="Anonymized feature payload for cross-firm LoRA training.",
    )
    anonymized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


__all__ = ["VerdictCorrection"]
