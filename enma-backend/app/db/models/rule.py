"""CA Firm Rules model — the unified RAG intelligence layer.

Rules are either firm-wide (``client_id IS NULL``) or client-specific.
Each rule carries a 1024-dim pgvector embedding for semantic search,
combined with relational pre-filtering for hybrid RAG retrieval.

Sources: ``human_correction``, ``manual_entry``, ``system_learned``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
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


class CaFirmRule(Base):
    """A firm intelligence rule with vector embedding for hybrid search."""

    __tablename__ = "ca_firm_rules"
    __table_args__ = (
        Index(
            "idx_firm_rules_lookup",
            "ca_firm_id",
            "client_id",
            postgresql_where=text("is_active = TRUE"),
        ),
        # IVFFlat index for cosine similarity — 100 lists is fine for <10K vectors per firm.
        Index(
            "idx_firm_rules_vector",
            "rule_embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"rule_embedding": "vector_cosine_ops"},
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
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="SET NULL"),
        nullable=True,
        comment="NULL = firm-wide rule; set = client-specific.",
    )
    rule_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="correction | preference | exception | procedure",
    )
    rule_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Human-readable rule description.",
    )
    rule_embedding = mapped_column(
        Vector(1024),
        nullable=True,
        comment="pgvector 1024-dim embedding for semantic search.",
    )
    source: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="human_correction | manual_entry | system_learned",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("TRUE"),
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


__all__ = ["CaFirmRule"]
