"""Infrastructure models — idempotency, identity resolution, notifications.

These tables support operational concerns rather than business entities.
They are write-heavy, append-only, and periodically cleaned up.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IdempotencyLog(Base):
    """Deduplication log for gateway-to-backend message delivery."""

    __tablename__ = "idempotency_log"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", name="uq_idempotency_chat_msg"),
        Index("idx_idempotency_lookup", "chat_id", "message_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    ca_firm_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="May be NULL if firm resolution hasn't happened yet.",
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    update_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payload_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="SHA-256 of the base64-decoded JSON envelope.",
    )
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


class IdentityResolutionLog(Base):
    """Audit log for the 5-stage identity resolution cascade."""

    __tablename__ = "identity_resolution_log"

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
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    stage_reached: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="1=session, 2=caption, 3=gstin, 4=vendor, 5=explicit_ask",
    )
    matched_client_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="SET NULL"),
        nullable=True,
    )
    confidence: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="HIGH | MEDIUM | EXPLICIT",
    )
    resolution_time_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


class ClientNotification(Base):
    """Tracks notifications sent to clients for cooldown enforcement."""

    __tablename__ = "client_notifications"
    __table_args__ = (
        Index(
            "idx_notifications_cooldown",
            "ca_firm_id",
            "client_id",
            "notification_type",
            "cooldown_until",
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
    notification_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="document_chase | deadline_reminder | filing_reminder",
    )
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    cooldown_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )





__all__ = [
    "ClientNotification",
    "IdempotencyLog",
    "IdentityResolutionLog",
]
