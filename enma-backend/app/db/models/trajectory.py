"""Agentic-trajectory model — one row per supervisor turn.

Append-only. Materialised as a TimescaleDB hypertable on
``created_at`` (see migration 012). The composite PK ``(created_at,
id)`` is what the hypertable requires; there are no FKs into this
table because trajectories are an event log, not a referenced entity.
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
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AgenticTrajectory(Base):
    """One supervisor turn captured for the LoRA training corpus."""

    __tablename__ = "agentic_trajectories"
    __table_args__ = (
        Index("ix_agentic_trajectories_firm_time", "ca_firm_id", "created_at"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
        primary_key=True,
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
        primary_key=True,
    )
    ca_firm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ca_firms.id", ondelete="CASCADE"),
        nullable=False,
    )
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_text: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls_made: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment="Sequence of {name, args, error?} entries.",
    )
    final_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    llm_calls: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment="Per-LLM-call detail (model, tokens, ms). Optional.",
    )


__all__ = ["AgenticTrajectory"]
