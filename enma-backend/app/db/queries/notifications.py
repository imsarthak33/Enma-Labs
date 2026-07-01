"""Client-notification ledger — cooldown enforcement.

Used by the Phase 7 client chase to honour the 7-day quiet window
between successive document-chase pings to the same client. Lookups
and inserts are tenant-scoped via :class:`BaseQuery`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Final, cast

from sqlalchemy import select

from app.db.models.infrastructure import ClientNotification
from app.db.queries.base import BaseQuery

__all__ = [
    "CHASE_COOLDOWN_DAYS",
    "NOTIFICATION_COMPLETENESS_CHASE",
    "NOTIFICATION_DOCUMENT_CHASE",
    "NotificationQuery",
]

# CA-facing chase (Phase 7): "these clients have no docs yet" → the CA.
NOTIFICATION_DOCUMENT_CHASE: Final[str] = "document_chase"
# Client-facing chase (Phase 8c): "we still need X from you" → the client on
# their bound channel. Separate type so its cooldown is independent — a client
# nudge must not suppress the CA's own overview chase, and vice versa.
NOTIFICATION_COMPLETENESS_CHASE: Final[str] = "completeness_chase"
CHASE_COOLDOWN_DAYS: Final[int] = 7


class NotificationQuery(BaseQuery):
    """Tenant-scoped client-notification operations."""

    async def was_recently_notified(
        self,
        *,
        client_id: uuid.UUID | str,
        notification_type: str = NOTIFICATION_DOCUMENT_CHASE,
        now: datetime | None = None,
    ) -> bool:
        """True iff an unexpired cooldown row exists for this client + kind."""
        cid = (
            client_id if isinstance(client_id, uuid.UUID) else uuid.UUID(str(client_id))
        )
        clock = now or datetime.now(UTC)
        stmt = (
            select(ClientNotification.id)
            .where(ClientNotification.ca_firm_id == self.ca_firm_id)
            .where(ClientNotification.client_id == cid)
            .where(ClientNotification.notification_type == notification_type)
            .where(ClientNotification.cooldown_until > clock)
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def record_notification(
        self,
        *,
        client_id: uuid.UUID | str,
        notification_type: str = NOTIFICATION_DOCUMENT_CHASE,
        cooldown_days: int = CHASE_COOLDOWN_DAYS,
        now: datetime | None = None,
    ) -> ClientNotification:
        """Persist a notification + cooldown after a successful Telegram send."""
        cid = (
            client_id if isinstance(client_id, uuid.UUID) else uuid.UUID(str(client_id))
        )
        clock = now or datetime.now(UTC)
        row = ClientNotification(
            client_id=cid,
            notification_type=notification_type,
            sent_at=clock,
            cooldown_until=clock + timedelta(days=cooldown_days),
        )
        return cast(ClientNotification, await self._insert(row))
