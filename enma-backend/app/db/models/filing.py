"""Filing Approval model — immutable audit trail.

Once a filing is approved via the ``ENMA APPROVE FILING`` protocol,
a snapshot of the entire filing state is locked into this table. The
record is immutable — no UPDATE or DELETE is permitted in application
code. The ``(ca_firm_id, filing_month, filing_year)`` triple is unique.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FilingApproval(Base):
    """An immutable record of a filing approval event."""

    __tablename__ = "filing_approvals"
    __table_args__ = (
        UniqueConstraint(
            "ca_firm_id",
            "filing_month",
            "filing_year",
            name="uq_filing_approvals_firm_period",
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
    filing_month: Mapped[int] = mapped_column(Integer, nullable=False)
    filing_year: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_by_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    filing_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="Full state at time of approval — document IDs, totals, etc.",
    )
    approval_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="SHA-256 of the filing_snapshot for integrity verification.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


__all__ = ["FilingApproval"]
