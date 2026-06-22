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

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.firm import CaFirm, FirmUser

__all__ = [
    "create_firm_with_admin",
    "find_firm_by_admin_chat_id",
    "get_firm_by_id",
    "link_chat_to_firm",
    "list_all_firms",
]


async def find_firm_by_admin_chat_id(session: AsyncSession, chat_id: int) -> CaFirm | None:
    """Return the firm that owns this chat identifier across either channel.

    Lookup order:
      1. ``ca_firms.admin_chat_id`` — legacy single-admin Telegram path.
         Existing Telegram firms continue to resolve here at zero cost.
      2. ``firm_users.chat_id`` — W4-P3 multi-channel join. WhatsApp
         users are bound here with ``chat_id`` synthesized from their
         E.164 phone digits (``+91…`` → ``91…``), so the same column
         resolves either origin uniformly.

    A firm with both channels live (Telegram on ``admin_chat_id`` +
    WhatsApp on a ``firm_users`` row) hits path 1 for Telegram envelopes
    and path 2 for WA envelopes; the same :class:`CaFirm` row is
    returned either way.
    """
    stmt = select(CaFirm).where(CaFirm.admin_chat_id == chat_id).limit(1)
    result = await session.execute(stmt)
    firm = result.scalar_one_or_none()
    if firm is not None:
        return firm

    stmt = (
        select(CaFirm)
        .join(FirmUser, FirmUser.ca_firm_id == CaFirm.id)
        .where(FirmUser.chat_id == chat_id)
        .limit(1)
    )
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


async def create_firm_with_admin(
    session: AsyncSession,
    *,
    firm_name: str,
    admin_chat_id: int,
    telegram_bot_token: str,
) -> CaFirm:
    """Create a new CA firm and its admin FirmUser in one transaction.

    Legacy path — only used when a user types `/start` with no payload
    and no pre-existing frontend row. The web onboarding is the canonical
    creation path. Caller commits.
    """
    firm = CaFirm(
        firm_name=firm_name,
        admin_chat_id=admin_chat_id,
        telegram_chat_id=str(admin_chat_id),
        telegram_linked_at=datetime.now(UTC),
        telegram_bot_token=telegram_bot_token,
        onboarding_completed=True,
    )
    session.add(firm)
    await session.flush()  # Populate firm.id

    admin_user = FirmUser(
        ca_firm_id=firm.id,
        chat_id=admin_chat_id,
        role="admin",
    )
    session.add(admin_user)
    return firm


async def link_chat_to_firm(
    session: AsyncSession,
    *,
    firm: CaFirm,
    chat_id: int,
    telegram_bot_token: str,
) -> CaFirm:
    """Bind a Telegram chat to a firm row created by the web onboarding.

    Idempotent: re-linking the same chat_id is a no-op. Linking a *new*
    chat_id over an existing link is rejected by the caller — silent
    overwrite would orphan whoever owned the prior link.

    Writes both ``admin_chat_id`` (BigInteger, used by the bot pipeline)
    and ``telegram_chat_id`` (text, used by the frontend dashboard).
    Caller commits.
    """
    firm.admin_chat_id = chat_id
    firm.telegram_chat_id = str(chat_id)
    firm.telegram_linked_at = datetime.now(UTC)
    if not firm.telegram_bot_token:
        firm.telegram_bot_token = telegram_bot_token
    firm.onboarding_completed = True

    # Insert the admin FirmUser row if one doesn't exist yet.
    existing_user = await session.execute(
        select(FirmUser).where(
            FirmUser.ca_firm_id == firm.id,
            FirmUser.chat_id == chat_id,
        )
    )
    if existing_user.scalar_one_or_none() is None:
        session.add(
            FirmUser(
                ca_firm_id=firm.id,
                chat_id=chat_id,
                role="admin",
                display_name=firm.ca_name,
            )
        )

    return firm
