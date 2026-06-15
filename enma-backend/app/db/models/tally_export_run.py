"""Tally Prime export audit trail.

Every successful ``export_to_tally`` supervisor-tool call writes one row
here. The row is immutable from application code — there is no UPDATE or
DELETE path. We keep this as a forward-only audit log so a CA can answer
"when did I last export July 2025 for CLEIND, and what was the
voucher count" without re-running the export.

Email-related columns (``recipient_email``, ``sent_at``) are intentionally
absent in W3 — outbound email arrives in W4a once we own a verified
sending domain. The W3 export delivers the XML as a Telegram document
attachment to the CA chat directly; ``exported_by_chat_id`` is the
authoritative "where it went".
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TallyExportRun(Base):
    """One Tally Prime XML export delivered to the CA."""

    __tablename__ = "tally_export_runs"

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
    filing_month: Mapped[int] = mapped_column(Integer, nullable=False)
    filing_year: Mapped[int] = mapped_column(Integer, nullable=False)
    voucher_count: Mapped[int] = mapped_column(Integer, nullable=False)
    file_sha256: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="SHA-256 of the emitted XML bytes for integrity verification.",
    )
    exported_by_chat_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="Telegram chat_id the document was delivered to.",
    )
    exported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


__all__ = ["TallyExportRun"]
