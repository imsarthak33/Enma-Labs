"""Client CRUD operations — all scoped to ca_firm_id."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import cast

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.client import Client
from app.db.models.document import Document
from app.db.models.firm import CaFirm
from app.db.queries.base import BaseQuery


class ClientQuery(BaseQuery):
    """Tenant-scoped client operations."""

    async def list_active(self) -> Sequence[Client]:
        """Return all active clients for this firm."""
        stmt = self._scoped_select(Client).where(Client.is_active.is_(True))
        return await self._fetch_all(stmt)

    async def list_with_gsp_consent(self) -> Sequence[Client]:
        """Active clients with a GSTIN and an unexpired GSP consent token.

        The GSTR-2B monthly-pull cron iterates these — a client without a
        stored, unexpired token (or without a GSTIN) is simply skipped, so
        the pull stays dormant until consent is captured.
        """
        from datetime import UTC, datetime

        stmt = (
            self._scoped_select(Client)
            .where(Client.is_active.is_(True))
            .where(Client.gstin.is_not(None))
            .where(Client.gsp_auth_token.is_not(None))
            .where(Client.gsp_auth_token_expires_at > datetime.now(UTC))
        )
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
        cid = client_id if isinstance(client_id, uuid.UUID) else uuid.UUID(str(client_id))
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

    async def search_by_name_ranked(
        self,
        name: str,
        *,
        min_similarity: float = 0.55,
        limit: int = 3,
    ) -> list[tuple[Client, float]]:
        """R3 — pg_trgm-ranked fuzzy search returning (client, similarity).

        Returned list is ordered by similarity descending. Empty when
        nothing crosses ``min_similarity``. The caller decides how to
        treat the top result (e.g., ≥ 0.85 = auto-resolve, 0.55-0.85 =
        disambiguate, < 0.55 = fall through to supervisor).

        Used by the agentic routing reply (R3): when an open
        pending_assignment is awaiting a client, free-form user replies
        like "CLEIND" should resolve it without forcing slash syntax.
        """
        clean = name.strip()
        if len(clean) < 2:
            return []
        sim_trade = func.similarity(Client.trade_name, clean)
        sim_legal = func.similarity(func.coalesce(Client.legal_name, ""), clean)
        similarity = func.greatest(sim_trade, sim_legal).label("similarity")
        stmt = (
            select(Client, similarity)
            .where(Client.ca_firm_id == self.ca_firm_id)
            .where(Client.is_active.is_(True))
            .where(similarity >= min_similarity)
            .order_by(similarity.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [(row[0], float(row[1])) for row in result.all()]

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

    async def update(self, client_id: uuid.UUID | str, **kwargs: object) -> Client | None:
        """Update client fields (firm-scoped)."""
        return await self._update(Client, client_id, **kwargs)

    # ----- Phase 7: client chase -------------------------------------------

    async def list_missing_filing_docs(
        self, *, month: int, year: int
    ) -> Sequence[Client]:
        """Active clients with zero documents in the given filing period.

        Used by the 28th-of-month client chase: callers iterate the
        result, apply the per-client 7-day cooldown, and ping each up to
        the batch cap.
        """
        has_doc_subq = (
            select(Document.id)
            .where(Document.ca_firm_id == self.ca_firm_id)
            .where(Document.client_id == Client.id)
            .where(Document.filing_period_month == month)
            .where(Document.filing_period_year == year)
        )
        stmt = (
            self._scoped_select(Client)
            .where(Client.is_active.is_(True))
            .where(~exists(has_doc_subq))
            .order_by(Client.trade_name.asc())
        )
        return await self._fetch_all(stmt)


async def find_client_with_firm(
    session: AsyncSession, client_id: uuid.UUID
) -> tuple[Client, CaFirm] | None:
    """Cross-firm: resolve a client UUID to its (client, owning firm).

    Used by the email-ingest cron to route an auto-ingested statement to the
    right client + firm — the inbound mail carries the client UUID in its
    address, but the poll has no firm context. This is a sanctioned
    cross-firm read (same exemption as :func:`list_all_firms`); allowed
    caller list (CI grep): ``app/api/routes/cron.py`` only.
    """
    stmt = (
        select(Client, CaFirm)
        .join(CaFirm, CaFirm.id == Client.ca_firm_id)
        .where(Client.id == client_id)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    return (row[0], row[1])


async def find_client_by_telegram_chat_id(
    session: AsyncSession, chat_id: int
) -> tuple[Client, CaFirm] | None:
    """Cross-firm: resolve a bound 1:1 client chat to its (client, firm).

    The worker uses this to route a client's inbound documents (ADR-017):
    the chat is bound to exactly one client via the deep link, so a message
    from a non-CA chat that matches here belongs to that client. Same
    cross-firm exemption as :func:`find_client_with_firm`; allowed callers
    (CI grep): ``app/api/routes/worker.py``.
    """
    stmt = (
        select(Client, CaFirm)
        .join(CaFirm, CaFirm.id == Client.ca_firm_id)
        .where(Client.telegram_chat_id == chat_id)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    return (row[0], row[1])


__all__ = [
    "ClientQuery",
    "find_client_by_telegram_chat_id",
    "find_client_with_firm",
]
