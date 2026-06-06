"""Envelope verification dependency.

Inbound contract — Phase 2 gateway sends:

    POST /worker/<kind>
    Headers:
        X-Enma-Api-Key:       <shared API key>
        X-Enma-Signature:     <hex sha256 hmac over payload_b64>
        X-Enma-Envelope-Version: 1
    Body (JSON):
        {
            "v": 1,
            "kind": "...",
            "payload_b64": "<base64-encoded inner-envelope JSON>",
            "signature": "<same hex sha256, redundant — IGNORED here>"
        }

Inner envelope (after base64-decode of ``payload_b64``):
    {
        "v": 1,
        "kind": "...",
        "issued_at": "<ISO-8601 UTC>",
        "nonce": "<uuid>",
        "chat_id": <int|null>,
        "message_id": <int|null>,
        "update_id": <int|null>,
        "payload": { ... handler-specific data ... }
    }

We verify the API key, the HMAC over ``payload_b64``, decode the inner JSON,
validate shape, and reject anything older than ``REPLAY_WINDOW_SECONDS``.
The signature inside the body is ignored — only the header is authenticated.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Final

from fastapi import Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import settings
from app.logging_setup import get_logger

_log = get_logger(__name__)

ENVELOPE_VERSION: Final[int] = 1
REPLAY_WINDOW_SECONDS: Final[int] = 300  # 5 minutes
ALLOWED_KINDS: Final[frozenset[str]] = frozenset(
    {"document", "document_batch", "command", "voice", "callback"}
)
# Phase 7 — gateway-driven cron kinds. Verified by ``verify_cron_envelope``
# which uses identical HMAC + freshness checks but a different allow-list
# (these envelopes carry ``chat_id=null`` and no Telegram message_id).
CRON_KINDS: Final[frozenset[str]] = frozenset(
    {
        "cron_task_heartbeat",
        "cron_morning_briefing",
        "cron_client_chase",
        "cron_idempotency_cleanup",
    }
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class DecodedEnvelope(BaseModel):
    """The validated, decoded inner envelope handed to a route handler."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    v: int = Field(..., description="Envelope schema version.")
    kind: str = Field(..., description="One of ALLOWED_KINDS.")
    issued_at: datetime
    nonce: str
    chat_id: int | None
    message_id: int | None = None
    update_id: int | None = None
    payload: dict[str, Any]


# ---------------------------------------------------------------------------
# Failure helpers
# ---------------------------------------------------------------------------


def _unauthorized(reason: str) -> HTTPException:
    # We log the reason internally, but the response body stays generic so
    # an attacker cannot probe the verification pipeline by error string.
    _log.warning("envelope_unauthorized", reason=reason)
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="envelope verification failed",
    )


def _bad_request(reason: str) -> HTTPException:
    _log.warning("envelope_bad_request", reason=reason)
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"envelope rejected: {reason}",
    )


# ---------------------------------------------------------------------------
# Verification pipeline
# ---------------------------------------------------------------------------


def _check_api_key(api_key: str | None) -> None:
    if api_key is None:
        raise _unauthorized("missing api key header")
    expected = settings.backend_api_key.get_secret_value()
    if not hmac.compare_digest(api_key.encode(), expected.encode()):
        raise _unauthorized("api key mismatch")


def _verify_signature(payload_b64: str, signature_hex: str) -> None:
    """Recompute HMAC-SHA256 over ``payload_b64`` and constant-time compare."""
    secret = settings.gateway_hmac_secret.get_secret_value().encode()
    expected = hmac.new(secret, payload_b64.encode("ascii"), "sha256").hexdigest()
    if not hmac.compare_digest(expected, signature_hex.lower()):
        raise _unauthorized("hmac signature mismatch")


def _decode_inner(payload_b64: str) -> dict[str, Any]:
    try:
        raw = base64.b64decode(payload_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _bad_request("payload_b64 is not valid base64") from exc
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _bad_request("payload_b64 does not decode to JSON") from exc
    if not isinstance(decoded, dict):
        raise _bad_request("inner envelope must be a JSON object")
    return decoded


def _check_freshness(issued_at: datetime) -> None:
    # issued_at must be tz-aware; Pydantic gives us that from ISO-8601 Zulu.
    if issued_at.tzinfo is None:
        raise _bad_request("issued_at must be timezone-aware")
    now = datetime.now(UTC)
    delta = now - issued_at
    if delta > timedelta(seconds=REPLAY_WINDOW_SECONDS):
        raise _unauthorized(f"envelope too old (age={delta.total_seconds():.0f}s)")
    # Tolerate small clock skew — but a future-dated envelope by more than
    # the same window is also suspicious.
    if -delta > timedelta(seconds=REPLAY_WINDOW_SECONDS):
        raise _unauthorized(f"envelope too far in future (skew={-delta.total_seconds():.0f}s)")


# ---------------------------------------------------------------------------
# Dependency entrypoint
# ---------------------------------------------------------------------------


async def verify_envelope(
    request: Request,
    x_enma_api_key: Annotated[str | None, Header(alias="X-Enma-Api-Key")] = None,
    x_enma_signature: Annotated[str | None, Header(alias="X-Enma-Signature")] = None,
    x_enma_envelope_version: Annotated[str | None, Header(alias="X-Enma-Envelope-Version")] = None,
) -> DecodedEnvelope:
    """Verify the inbound envelope and return its decoded form."""

    _check_api_key(x_enma_api_key)

    if x_enma_signature is None:
        raise _unauthorized("missing signature header")

    if x_enma_envelope_version is not None and x_enma_envelope_version != str(ENVELOPE_VERSION):
        raise _bad_request(f"unsupported envelope version: {x_enma_envelope_version}")

    # Read the raw body once; FastAPI would otherwise consume it for us when
    # we declare a model parameter.
    raw_body = await request.body()
    if not raw_body:
        raise _bad_request("empty body")

    try:
        outer = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _bad_request("outer body is not JSON") from exc

    if not isinstance(outer, dict):
        raise _bad_request("outer body must be a JSON object")

    if outer.get("v") != ENVELOPE_VERSION:
        raise _bad_request(f"outer envelope version mismatch: {outer.get('v')!r}")

    payload_b64 = outer.get("payload_b64")
    if not isinstance(payload_b64, str) or not payload_b64:
        raise _bad_request("outer envelope missing payload_b64")

    # Verify BEFORE decoding the inner JSON — fewer attacker-controlled code
    # paths on the rejection branch.
    _verify_signature(payload_b64, x_enma_signature)

    inner_dict = _decode_inner(payload_b64)

    try:
        envelope = DecodedEnvelope.model_validate(inner_dict)
    except ValidationError as exc:
        raise _bad_request(f"inner envelope schema invalid: {exc.errors()[:3]}") from exc

    if envelope.v != ENVELOPE_VERSION:
        raise _bad_request(f"inner envelope version mismatch: {envelope.v}")

    if envelope.kind not in ALLOWED_KINDS:
        raise _bad_request(f"unknown envelope kind: {envelope.kind}")

    # The outer and inner kind MUST agree — a mismatch indicates either a
    # routing bug in the gateway or active tampering before signing.
    if outer.get("kind") != envelope.kind:
        raise _bad_request("outer/inner kind mismatch")

    _check_freshness(envelope.issued_at)

    return envelope


VerifiedEnvelopeDep = Annotated[DecodedEnvelope, Depends(verify_envelope)]


# ---------------------------------------------------------------------------
# Cron envelope verifier (Phase 7)
# ---------------------------------------------------------------------------


async def verify_cron_envelope(  # noqa: PLR0912 — linear validator, branches are early returns
    request: Request,
    x_enma_api_key: Annotated[str | None, Header(alias="X-Enma-Api-Key")] = None,
    x_enma_signature: Annotated[str | None, Header(alias="X-Enma-Signature")] = None,
    x_enma_envelope_version: Annotated[str | None, Header(alias="X-Enma-Envelope-Version")] = None,
) -> DecodedEnvelope:
    """Verify an inbound cron envelope.

    Identical HMAC + freshness checks to :func:`verify_envelope`, but:

    * ``kind`` must be one of :data:`CRON_KINDS`.
    * ``chat_id`` MUST be ``None`` — cron has no Telegram chat target.
    * The payload MUST carry ``scheduled_at`` (ISO-8601, tz-aware) so the
      idempotency middleware can derive a stable dedup key.

    See ADR-007 §Decision 2 for the dedup-key rationale.
    """
    _check_api_key(x_enma_api_key)

    if x_enma_signature is None:
        raise _unauthorized("missing signature header")

    if x_enma_envelope_version is not None and x_enma_envelope_version != str(ENVELOPE_VERSION):
        raise _bad_request(f"unsupported envelope version: {x_enma_envelope_version}")

    raw_body = await request.body()
    if not raw_body:
        raise _bad_request("empty body")

    try:
        outer = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _bad_request("outer body is not JSON") from exc
    if not isinstance(outer, dict):
        raise _bad_request("outer body must be a JSON object")
    if outer.get("v") != ENVELOPE_VERSION:
        raise _bad_request(f"outer envelope version mismatch: {outer.get('v')!r}")

    payload_b64 = outer.get("payload_b64")
    if not isinstance(payload_b64, str) or not payload_b64:
        raise _bad_request("outer envelope missing payload_b64")

    _verify_signature(payload_b64, x_enma_signature)

    inner_dict = _decode_inner(payload_b64)

    try:
        envelope = DecodedEnvelope.model_validate(inner_dict)
    except ValidationError as exc:
        raise _bad_request(f"inner envelope schema invalid: {exc.errors()[:3]}") from exc

    if envelope.v != ENVELOPE_VERSION:
        raise _bad_request(f"inner envelope version mismatch: {envelope.v}")
    if envelope.kind not in CRON_KINDS:
        raise _bad_request(f"not a cron envelope kind: {envelope.kind}")
    if outer.get("kind") != envelope.kind:
        raise _bad_request("outer/inner kind mismatch")
    if envelope.chat_id is not None:
        # Cron envelopes are firm-fanout-only — a chat_id would muddle the
        # per-firm scheduling and would also collide with the user idempotency
        # key. Reject early.
        raise _bad_request("cron envelope must not carry chat_id")
    if not isinstance(envelope.payload.get("scheduled_at"), str):
        raise _bad_request("cron envelope payload missing 'scheduled_at'")

    _check_freshness(envelope.issued_at)

    return envelope


VerifiedCronEnvelopeDep = Annotated[DecodedEnvelope, Depends(verify_cron_envelope)]


__all__ = [
    "ALLOWED_KINDS",
    "CRON_KINDS",
    "DecodedEnvelope",
    "ENVELOPE_VERSION",
    "REPLAY_WINDOW_SECONDS",
    "VerifiedCronEnvelopeDep",
    "VerifiedEnvelopeDep",
    "verify_cron_envelope",
    "verify_envelope",
]
