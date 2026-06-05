"""Idempotency dependency.

Runs after :func:`app.api.middleware.envelope_verify.verify_envelope` and
before the route body. Uses the decoded envelope's ``(chat_id, message_id)``
as the dedup key.

Behaviour:
    * New envelope → row inserted, verdict ``(is_duplicate=False, log_id=<uuid>)``.
    * Repeat envelope → verdict ``(is_duplicate=True, log_id=None)``.

Worker routes inspect ``verdict.is_duplicate`` and short-circuit (return 200
without re-acking the user or re-spawning the pipeline) when ``True``.

We do NOT enforce idempotency on envelopes without a ``message_id`` (e.g.,
cron-triggered or callback-only updates that don't have a Telegram message
id). Such envelopes get ``log_id=None, is_duplicate=False`` and the route is
responsible for any custom deduplication.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.middleware.envelope_verify import DecodedEnvelope, verify_envelope
from app.db.queries import idempotency as idem_queries
from app.db.session import get_session
from app.logging_setup import get_logger

_log = get_logger(__name__)


class IdempotencyVerdict(BaseModel):
    """The outcome of the idempotency check for a given envelope."""

    model_config = ConfigDict(frozen=True)

    is_duplicate: bool
    log_id: uuid.UUID | None = Field(
        default=None,
        description="ID of the inserted idempotency row, when newly inserted.",
    )


async def check_idempotency(
    envelope: Annotated[DecodedEnvelope, Depends(verify_envelope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IdempotencyVerdict:
    """Insert-if-new on the idempotency log; returns the verdict."""

    if envelope.chat_id is None or envelope.message_id is None:
        # Cron / callback-style envelopes don't carry a Telegram message id.
        # Routes that care about deduping these implement their own scheme.
        return IdempotencyVerdict(is_duplicate=False, log_id=None)

    payload_hash = idem_queries.hash_payload(envelope.nonce)
    inserted = await idem_queries.insert_if_new(
        session,
        chat_id=envelope.chat_id,
        message_id=envelope.message_id,
        update_id=envelope.update_id,
        payload_hash=payload_hash,
    )

    if inserted is None:
        _log.info(
            "envelope_duplicate_dropped",
            chat_id=envelope.chat_id,
            message_id=envelope.message_id,
            kind=envelope.kind,
        )
        return IdempotencyVerdict(is_duplicate=True, log_id=None)

    return IdempotencyVerdict(is_duplicate=False, log_id=inserted)


IdempotencyDep = Annotated[IdempotencyVerdict, Depends(check_idempotency)]


__all__ = ["IdempotencyDep", "IdempotencyVerdict", "check_idempotency"]
