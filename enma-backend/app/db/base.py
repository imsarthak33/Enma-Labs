"""Declarative base and shared column mixins.

Every ORM model inherits from ``Base``. The ``TenantMixin`` adds the
mandatory ``ca_firm_id`` column and common audit timestamps that appear
on every tenant-scoped table.

All columns use BOTH ``default`` (Python-side, for tests/SQLite) and
``server_default`` (PostgreSQL-side, for production). This is the
recommended SQLAlchemy dual-default pattern.

Phase 1 deliverable — canonical single source of metadata for Alembic.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Root declarative base for all Enma ORM models."""

    pass


class TenantMixin:
    """Mixin injecting ``ca_firm_id`` + audit timestamps.

    Every tenant-scoped table MUST use this mixin.  The ``ca_firm_id``
    column is NOT NULL — a row without firm scoping is a security bug.
    """

    ca_firm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
        comment="Owning CA firm — mandatory tenant scope.",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=text("NOW()"),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        server_default=text("NOW()"),
    )


class AuditTimestampMixin:
    """Lighter mixin for tables that only need created_at (immutable logs)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=text("NOW()"),
    )


__all__ = ["AuditTimestampMixin", "Base", "TenantMixin", "_utcnow"]
