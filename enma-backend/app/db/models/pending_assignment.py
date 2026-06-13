"""Pending-assignment model — transient routing state for stage 5.

When the identity resolver cannot pick a client deterministically it
queues a row here, sends the CA a "which client?" prompt with inline
buttons, and waits for the callback. The row is short-lived; the
resolver and a periodic cleanup both treat any row past ``expires_at``
as abandoned.

Scoped to ``ca_firm_id`` with full RLS (see migration 003).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PendingAssignment(Base):
    """A document awaiting a CA's client-pick callback."""

    __tablename__ = "pending_assignments"
    __table_args__ = (
        Index(
            "idx_pending_assignments_open",
            "ca_firm_id",
            "chat_id",
            "expires_at",
            postgresql_where=text("resolved_at IS NULL"),
        ),
        CheckConstraint(
            "jsonb_typeof(file_ids) = 'array'",
            name="ck_pending_assignments_file_ids_is_array",
        ),
        CheckConstraint(
            "jsonb_typeof(candidate_client_ids) = 'array'",
            name="ck_pending_assignments_candidates_is_array",
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
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    file_ids: Mapped[list[Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    candidate_client_ids: Mapped[list[Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    prompt_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resolved_client_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    extraction: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment=(
            "Cached extract_only output (R1). Populated when an upload "
            "reaches EXPLICIT_ASK so /assign + inline-button confirmation "
            "do not re-OCR the file. R2 consumes this; R1 leaves it NULL."
        ),
    )
    extraction_document_type: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="Classifier's document_type when extraction was cached.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


__all__ = ["PendingAssignment"]
