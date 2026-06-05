"""Client CRUD operations — all scoped to ca_firm_id."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import cast

from sqlalchemy import or_

from app.db.models.client import Client
from app.db.queries.base import BaseQuery


class ClientQuery(BaseQuery):
    """Tenant-scoped client operations."""

    async def list_active(self) -> Sequence[Client]:
        """Return all active clients for this firm."""
        stmt = self._scoped_select(Client).where(Client.is_active.is_(True))
        return await self._fetch_all(stmt)

    async def first_active(self) -> Client | None:
        """Return the oldest active client for this firm.

        Phase 4 stub: used by the worker pipeline when identity
        resolution (Phase 6) hasn't picked a specific client yet. Will
        be replaced by the 5-stage resolver in Phase 6.
        """
        stmt = (
            self._scoped_select(Client)
            .where(Client.is_active.is_(True))
            .order_by(Client.created_at.asc())
            .limit(1)
        )
        return await self._fetch_one(stmt)

    async def get_by_id(self, client_id: uuid.UUID | str) -> Client | None:
        """Fetch a single client by ID (firm-scoped)."""
        cid = (
            client_id
            if isinstance(client_id, uuid.UUID)
            else uuid.UUID(str(client_id))
        )
        stmt = self._scoped_select(Client).where(Client.id == cid)
        return await self._fetch_one(stmt)

    async def get_by_gstin(self, gstin: str) -> Client | None:
        """Fetch a client by GSTIN (firm-scoped)."""
        stmt = self._scoped_select(Client).where(Client.gstin == gstin)
        return await self._fetch_one(stmt)

    async def search_by_name(self, name: str) -> Sequence[Client]:
        """Fuzzy search by trade_name or legal_name (firm-scoped).

        Uses ILIKE for simplicity; Phase 6 upgrades to pg_trgm similarity.
        """
        pattern = f"%{name}%"
        stmt = (
            self._scoped_select(Client)
            .where(Client.is_active.is_(True))
            .where(
                or_(
                    Client.trade_name.ilike(pattern),
                    Client.legal_name.ilike(pattern),
                )
            )
        )
        return await self._fetch_all(stmt)

    async def create(
        self,
        *,
        trade_name: str,
        legal_name: str | None = None,
        gstin: str | None = None,
        pan: str | None = None,
        state_code: str | None = None,
        address: str | None = None,
        contact_email: str | None = None,
        contact_phone: str | None = None,
    ) -> Client:
        """Insert a new client for this firm."""
        client = Client(
            trade_name=trade_name,
            legal_name=legal_name,
            gstin=gstin,
            pan=pan,
            state_code=state_code,
            address=address,
            contact_email=contact_email,
            contact_phone=contact_phone,
        )
        return cast(Client, await self._insert(client))

    async def deactivate(self, client_id: uuid.UUID | str) -> Client | None:
        """Soft-delete a client."""
        return await self._soft_delete(Client, client_id)

    async def update(
        self, client_id: uuid.UUID | str, **kwargs: object
    ) -> Client | None:
        """Update client fields (firm-scoped)."""
        return await self._update(Client, client_id, **kwargs)


__all__ = ["ClientQuery"]
