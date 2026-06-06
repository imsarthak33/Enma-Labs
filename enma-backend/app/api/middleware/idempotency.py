"""Idempotency dependency.

Runs after :func:`app.api.middleware.envelope_verify.verify_envelope` and
before the route body. Uses the decoded envelope's ``(chat_id, message_id)``
as the dedup key.

Behaviour:
    * New envelope → row inserted, verdict ``(is_duplicate=False, log_id=<uuid>)``.
    * Repeat envelope → verdict ``(is_duplicate=True, log_id=None)``.

Worker routes inspect ``verdict.is_duplicate`` and short-circuit (return 200
without re-acking the user or re-spawning the pipeline) when ``True``.

Cron envelopes
--------------
Cron-triggered envelopes have no Telegram ``message_id``. ADR-007 §Decision 2
defines a synthetic dedup key for them:

    chat_id    = 0  (no real Telegram chat has id 0)
    message_id = floor(scheduled_at_unix_seconds / 60)

That makes two cron firings inside the same minute dedupe (good — that's
exactly what we want a heartbeat-restart loop to do) without colliding
with any real user envelope.

Callback envelopes still carry a message_id and use the normal key.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.middleware.envelope_verify import (
    CRON_KINDS,
    DecodedEnvelope,
    verify_envelope,
)
from app.db.queries import idempotency as idem_queries
from app.db.session import get_session
from app.logging_setup import get_logger

_log = get_logger(__name__)

# Synthetic chat_id for cron envelopes — see module docstring.
CRON_CHAT_ID: int = 0


class IdempotencyVerdict(BaseModel):
    """The outcome of the idempotency check for a given envelope."""

    model_config = ConfigDict(frozen=True)

    is_duplicate: bool
    log_id: uuid.UUID | None = Field(
        default=None,
        description="ID of the inserted idempotency row, when newly inserted.",
    )


def _cron_dedup_key(envelope: DecodedEnvelope) -> tuple[int, int]:
    """Derive ``(chat_id, message_id)`` for a cron envelope.

    See ADR-007 §Decision 2. ``scheduled_at`` is required (validated by
    :func:`verify_cron_envelope`) and ISO-8601 with timezone.
    """
    raw = envelope.payload.get("scheduled_at")
    if not isinstance(raw, str):
        # Should be unreachable — the cron verifier rejects this earlier.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cron envelope missing scheduled_at",
        )
    try:
        scheduled_at = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"cron scheduled_at not ISO-8601: {raw!r}",
        ) from exc
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=UTC)
    minute = int(scheduled_at.timestamp()) // 60
    return CRON_CHAT_ID, minute


async def check_idempotency(
    envelope: Annotated[DecodedEnvelope, Depends(verify_envelope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IdempotencyVerdict:
    """Insert-if-new on the idempotency log; returns the verdict."""
    chat_id, message_id = _select_dedup_key(envelope)
    if chat_id is None or message_id is None:
        return IdempotencyVerdict(is_duplicate=False, log_id=None)

    payload_hash = idem_queries.hash_payload(envelope.nonce)
    inserted = await idem_queries.insert_if_new(
        session,
        chat_id=chat_id,
        message_id=message_id,
        update_id=envelope.update_id,
        payload_hash=payload_hash,
    )

    if inserted is None:
        _log.info(
            "envelope_duplicate_dropped",
            chat_id=chat_id,
            message_id=message_id,
            kind=envelope.kind,
        )
        return IdempotencyVerdict(is_duplicate=True, log_id=None)

    return IdempotencyVerdict(is_duplicate=False, log_id=inserted)


def _select_dedup_key(envelope: DecodedEnvelope) -> tuple[int | None, int | None]:
    """Pick the right (chat_id, message_id) key for the envelope kind."""
    if envelope.kind in CRON_KINDS:
        return _cron_dedup_key(envelope)
    if envelope.chat_id is None or envelope.message_id is None:
        return None, None
    return envelope.chat_id, envelope.message_id


IdempotencyDep = Annotated[IdempotencyVerdict, Depends(check_idempotency)]


__all__ = [
    "CRON_CHAT_ID",
    "IdempotencyDep",
    "IdempotencyVerdict",
    "check_idempotency",
]
