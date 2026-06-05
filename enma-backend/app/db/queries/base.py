"""SECURITY-CRITICAL: Base query class enforcing tenant isolation.

All database queries MUST inherit from this class. It enforces
``ca_firm_id`` scoping on every SELECT, INSERT, UPDATE, and DELETE.

Direct SQLAlchemy session usage outside this class hierarchy and
``db/session.py`` is BANNED. Any bypass triggers a CI failure and a
mandatory code-review flag.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, TypeVar

from sqlalchemy import Select, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


class SecurityError(Exception):
    """Raised when a query violates tenant-isolation rules."""


class BaseQuery:
    """Tenant-scoped query base.

    Every operation (read, write, update, soft-delete) is automatically
    constrained to the owning ``ca_firm_id``. Subclasses define
    model-specific convenience methods but never bypass scoping.
    """

    def __init__(self, session: AsyncSession, ca_firm_id: uuid.UUID | str) -> None:
        if not ca_firm_id:
            raise SecurityError(
                "ca_firm_id is required for all database operations"
            )
        self.session = session
        self.ca_firm_id = (
            ca_firm_id
            if isinstance(ca_firm_id, uuid.UUID)
            else uuid.UUID(str(ca_firm_id))
        )

    # -- SELECT helpers -------------------------------------------------------

    def _scoped_select(self, model: type[T]) -> Select[tuple[T]]:
        """Every SELECT starts with firm-level scoping. Non-negotiable."""
        return select(model).where(
            model.ca_firm_id == self.ca_firm_id  # type: ignore[attr-defined]
        )

    def _scoped_select_client(
        self, model: type[T], client_id: uuid.UUID | str
    ) -> Select[tuple[T]]:
        """Client-scoped queries add a second constraint layer."""
        cid = (
            client_id
            if isinstance(client_id, uuid.UUID)
            else uuid.UUID(str(client_id))
        )
        return self._scoped_select(model).where(
            model.client_id == cid  # type: ignore[attr-defined]
        )

    async def _fetch_all(self, stmt: Select[tuple[T]]) -> Sequence[T]:
        """Execute a SELECT and return all rows."""
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def _fetch_one(self, stmt: Select[tuple[T]]) -> T | None:
        """Execute a SELECT and return a single row or None."""
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # -- INSERT helper --------------------------------------------------------

    async def _insert(self, instance: Any) -> Any:
        """Every INSERT gets firm_id injected. Cannot be overridden."""
        if hasattr(instance, "ca_firm_id"):
            instance.ca_firm_id = self.ca_firm_id
        self.session.add(instance)
        await self.session.flush()
        return instance

    # -- UPDATE helper --------------------------------------------------------

    async def _update(
        self, model: type[T], record_id: uuid.UUID | str, **kwargs: Any
    ) -> T | None:
        """Every UPDATE is scoped to firm_id + record_id."""
        rid = (
            record_id
            if isinstance(record_id, uuid.UUID)
            else uuid.UUID(str(record_id))
        )
        stmt = (
            update(model)
            .where(model.id == rid)  # type: ignore[attr-defined]
            .where(model.ca_firm_id == self.ca_firm_id)  # type: ignore[attr-defined]
            .values(**kwargs)
            .returning(model)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one_or_none()

    # -- SOFT DELETE helper ---------------------------------------------------

    async def _soft_delete(self, model: type[T], record_id: uuid.UUID | str) -> T | None:
        """Soft delete only. Scoped to firm_id."""
        return await self._update(model, record_id, is_active=False)

    # -- HARD DELETE helper (infrastructure tables only) ----------------------

    async def _hard_delete(self, model: type[T], record_id: uuid.UUID | str) -> None:
        """Hard delete — use ONLY for infrastructure/log tables."""
        rid = (
            record_id
            if isinstance(record_id, uuid.UUID)
            else uuid.UUID(str(record_id))
        )
        stmt = (
            delete(model)
            .where(model.id == rid)  # type: ignore[attr-defined]
            .where(model.ca_firm_id == self.ca_firm_id)  # type: ignore[attr-defined]
        )
        await self.session.execute(stmt)
        await self.session.flush()


__all__ = ["BaseQuery", "SecurityError"]
