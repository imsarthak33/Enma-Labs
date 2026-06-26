"""Client model.

Clients are the businesses managed by a CA firm. Each client has a
GSTIN, PAN, trade name, and contact details. The ``(ca_firm_id, gstin)``
pair is unique — a firm cannot have two clients with the same GSTIN.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Client(Base):
    """A business entity managed by a CA firm."""

    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("ca_firm_id", "gstin", name="uq_clients_firm_gstin"),)

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
    trade_name: Mapped[str] = mapped_column(Text, nullable=False)
    legal_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    gstin: Mapped[str | None] = mapped_column(
        String(15),
        nullable=True,
        comment="15-char GSTIN. Validated at app layer via regex + checksum.",
    )
    pan: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
        comment="10-char PAN. Encrypted at rest.",
    )
    state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    # Phase 7b.2 — per-client GSP OTP-consent token + expiry for the GSTR-2B
    # monthly pull. Populated by the (GSP-specific) consent-acquisition flow;
    # the pull cron skips clients without an unexpired token. Should be stored
    # encrypted once the consent flow lands (mirror whatsapp_auth_token_encrypted).
    gsp_auth_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    gsp_auth_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Phase 8a (ADR-017) — the client's bound 1:1 Telegram chat, set when they
    # tap their deep link (t.me/<bot>?start=client_<uuid>). Routes their
    # documents + chase replies to this client. Partial-unique (migration 020).
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    gst_tds_deductor: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("FALSE"),
        comment=(
            "Section 51 GST-TDS deductor flag (government / PSU). "
            "Engine gates tds_amount on this. IT-Act TDS is out of scope."
        ),
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


__all__ = ["Client"]
