"""CA Firm and Firm User models.

A CA firm is the top-level tenant. Every piece of data in the system
belongs to exactly one firm. Firm users are the humans (CA principal,
partners, article assistants) who interact with Enma via Telegram.

Firm rows are now created by the web app (Next.js + Supabase auth)
during onboarding. The Telegram bot then links a chat to a pre-created
firm via the deep-link payload `/start <firm_uuid>`. Columns written
by the frontend are mirrored here so SQLAlchemy can read and update
them safely.
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
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

    # ── Identity (written by frontend) ─────────────────────────────────────
    supabase_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        unique=True,
        comment="Supabase auth.users.id — bridges frontend session to firm row.",
    )
    firm_name: Mapped[str] = mapped_column(Text, nullable=False)
    ca_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Telegram link (written by backend on /start <uuid>) ────────────────
    admin_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Same value as admin_chat_id, stored as text for the frontend's typing.",
    )
    telegram_linked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    telegram_bot_token: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Encrypted at rest — never log or expose.",
    )

    # ── Consent & onboarding state (written by frontend) ───────────────────
    onboarding_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    dpa_consented: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    dpa_consented_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    privacy_policy_consented: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    privacy_policy_consented_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    data_training_consent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )

    # ── Subscription & status ──────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE")
    )
    subscription_plan: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default=text("'trial'")
    )
    # Legacy column from earlier phases — kept for the cron + admin paths.
    subscription_tier: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default=text("'starter'")
    )
    max_clients: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("50")
    )

    # ── Timestamps ─────────────────────────────────────────────────────────
    last_login: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    # ── Misc metadata ──────────────────────────────────────────────────────
    known_groups: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # -- Relationships (lazy-loaded by default) --
    users: Mapped[list[FirmUser]] = relationship(
        back_populates="firm", cascade="all, delete-orphan"
    )


class FirmUser(Base):
    """A user within a CA firm (admin, partner, or member)."""

    __tablename__ = "firm_users"
    __table_args__ = (UniqueConstraint("ca_firm_id", "chat_id", name="uq_firm_users_firm_chat"),)

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
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    # -- Relationships --
    firm: Mapped[CaFirm] = relationship(back_populates="users")


__all__ = ["CaFirm", "FirmUser"]
