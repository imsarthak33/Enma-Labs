"""Firm lookup queries — DOCUMENTED BaseQuery exception #2.

The BaseQuery pattern enforces ``ca_firm_id`` on every operation. But
firm resolution is the *prerequisite* to having a ca_firm_id at all —
when a Telegram message arrives, we know only the chat_id and must
discover which firm (if any) owns it.

This module is therefore one of the two sanctioned exceptions to
BaseQuery. The other is :mod:`app.db.queries.idempotency`. Both are
read-only (mostly) and operate on a non-tenant-scoped lookup key.

Allowed callers
---------------
Only the worker pipeline runner and Phase 6's identity resolver may
import from here. CI greps for any other importer.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.firm import CaFirm

__all__ = ["find_firm_by_admin_chat_id", "get_firm_by_id", "list_all_firms"]


async def find_firm_by_admin_chat_id(session: AsyncSession, chat_id: int) -> CaFirm | None:
    """Return the firm whose admin Telegram chat_id matches ``chat_id``.

    Phase 4 assumes a 1:1 mapping (one Telegram account per firm); Phase
    6 introduces the ``firm_users`` join for multi-member firms. The
    Phase 4 stub queries ``ca_firms.admin_chat_id`` only.
    """
    stmt = select(CaFirm).where(CaFirm.admin_chat_id == chat_id).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_firm_by_id(session: AsyncSession, firm_id: uuid.UUID) -> CaFirm | None:
    """Return the firm with the given ID, or ``None`` if not found."""
    fid = firm_id if isinstance(firm_id, uuid.UUID) else uuid.UUID(cast(str, firm_id))
    stmt = select(CaFirm).where(CaFirm.id == fid).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_all_firms(session: AsyncSession) -> Sequence[CaFirm]:
    """Return every CA firm in the database.

    Phase 7 cron jobs fan out across all firms; the loop body inside
    each route is firm-scoped via :class:`BaseQuery`, so the iteration
    itself is the only place that touches the cross-firm view. Allowed
    caller list (CI grep): ``app/api/routes/cron.py`` only.
    """
    stmt = select(CaFirm).order_by(CaFirm.created_at.asc())
    result = await session.execute(stmt)
    return result.scalars().all()
