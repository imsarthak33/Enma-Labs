"""Bug-report model — captured via ``/bug "<text>"`` from any CA.

Lightweight. Severity defaults to ``'unspecified'`` because the slash
command form ``/bug "<text>"`` doesn't carry one — operator triage
sets it later. ``resolved_at`` is NULL until someone closes the row
out (P1 dashboard work).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BugReport(Base):
    """A single bug report submitted by a CA."""

    __tablename__ = "bug_reports"
    __table_args__ = (
        Index("ix_bug_reports_firm_time", "ca_firm_id", "created_at"),
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
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    severity: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'unspecified'"),
        comment="unspecified | low | medium | high | critical",
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


__all__ = ["BugReport"]
