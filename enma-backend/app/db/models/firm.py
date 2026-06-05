"""CA Firm and Firm User models.

A CA firm is the top-level tenant. Every piece of data in the system
belongs to exactly one firm. Firm users are the humans (CA principal,
partners, article assistants) who interact with Enma via Telegram.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class CaFirm(Base):
    """Top-level tenant: a Chartered Accountant firm."""

    __tablename__ = "ca_firms"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    firm_name: Mapped[str] = mapped_column(Text, nullable=False)
    admin_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_bot_token: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Encrypted at rest — never log or expose.",
    )
    subscription_tier: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default=text("'starter'")
    )
    max_clients: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("50")
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

    # -- Relationships (lazy-loaded by default) --
    users: Mapped[list[FirmUser]] = relationship(
        back_populates="firm", cascade="all, delete-orphan"
    )


class FirmUser(Base):
    """A user within a CA firm (admin, partner, or member)."""

    __tablename__ = "firm_users"
    __table_args__ = (
        UniqueConstraint("ca_firm_id", "chat_id", name="uq_firm_users_firm_chat"),
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
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'member'"),
        comment="admin | partner | member",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    # -- Relationships --
    firm: Mapped[CaFirm] = relationship(back_populates="users")


__all__ = ["CaFirm", "FirmUser"]
