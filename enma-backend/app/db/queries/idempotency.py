"""Idempotency-log query module — DOCUMENTED BaseQuery exception.

Every other query module in this codebase MUST inherit from
:class:`app.db.queries.base.BaseQuery` so that ``ca_firm_id`` scoping is
applied to every operation. This file is one of the *very* short list of
exceptions.

Why it's exempt
---------------
Idempotency is checked at the API edge — *before* we resolve the firm a
chat_id belongs to. Phase 6 will add firm resolution; until then the
idempotency log is keyed on ``(chat_id, message_id)`` alone, and the row's
``ca_firm_id`` is populated as ``NULL`` until later phases backfill it.

There is therefore no ``ca_firm_id`` to enforce here, and forcing the row
through BaseQuery would require a placeholder firm UUID that defeats the
audit trail.

Allowed callers
---------------
Only ``app.api.middleware.idempotency`` may call into this module. CI greps
for any other importer and fails the build.

Concurrency note
----------------
``INSERT ... ON CONFLICT DO NOTHING RETURNING id`` is the canonical race-free
"insert-if-new" pattern in Postgres. Two concurrent gateway redeliveries hit
the unique constraint on ``(chat_id, message_id)`` and only one returns a
row — the other gets an empty ``RETURNING`` result and we treat it as a
duplicate.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_setup import get_logger

_log = get_logger(__name__)


_INSERT_IF_NEW_SQL: Final[str] = """
INSERT INTO idempotency_log (chat_id, message_id, update_id, payload_hash, ca_firm_id)
VALUES (:chat_id, :message_id, :update_id, :payload_hash, NULL)
ON CONFLICT (chat_id, message_id) DO NOTHING
RETURNING id
"""


def hash_payload(payload_b64: str) -> str:
    """SHA-256 of the raw payload_b64 string — recorded for audit/debug only."""
    return hashlib.sha256(payload_b64.encode("ascii")).hexdigest()


async def insert_if_new(
    session: AsyncSession,
    *,
    chat_id: int,
    message_id: int,
    update_id: int | None,
    payload_hash: str,
) -> uuid.UUID | None:
    """Insert a new idempotency row or return ``None`` on conflict.

    Returns the inserted row's ``id`` when the (chat_id, message_id) pair is
    new; returns ``None`` if it was already logged (i.e. duplicate delivery).
    """
    result = await session.execute(
        text(_INSERT_IF_NEW_SQL),
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "update_id": update_id,
            "payload_hash": payload_hash,
        },
    )
    row = result.first()
    if row is None:
        _log.info(
            "idempotency_duplicate",
            chat_id=chat_id,
            message_id=message_id,
        )
        return None
    inserted_id = row[0]
    if not isinstance(inserted_id, uuid.UUID):
        inserted_id = uuid.UUID(str(inserted_id))
    await session.commit()
    return inserted_id


__all__ = ["hash_payload", "insert_if_new"]
