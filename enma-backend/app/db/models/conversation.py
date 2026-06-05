"""Conversation model — single unified chat log.

Every message turn (user, assistant, system) is stored here. The table
serves dual purposes:

1. **LLM context window** — recent turns are injected into prompts,
   older turns are compacted via sliding-window summarisation.
2. **Audit log** — the full uncompacted history is never deleted.

Scoped to ``(ca_firm_id, chat_id)`` with optional ``client_id`` for
per-client context isolation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Conversation(Base):
    """A single chat turn in a firm's conversation history."""

    __tablename__ = "conversations"
    __table_args__ = (
        Index(
            "idx_conversations_lookup",
            "ca_firm_id",
            "chat_id",
            "created_at",
            postgresql_using="btree",
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
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="SET NULL"),
        nullable=True,
        comment="NULL if no client context active.",
    )
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="user | assistant | system",
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Telegram message_id — NULL for system-generated turns.",
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


__all__ = ["Conversation"]
