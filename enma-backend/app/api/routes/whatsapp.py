"""Twilio WhatsApp inbound webhook (W4-P3).

Why this lives on the backend and not the gateway
-------------------------------------------------
Twilio requires an HTTPS endpoint. The gateway today has no public
ingress (it polls Telegram outbound); standing one up plus a cert is
~hours of infra. The backend already has an ALB and is publicly
reachable. So the WA webhook lives here and reuses the existing
worker pipeline by constructing a :class:`DecodedEnvelope` directly
and spawning the same ``_run_command_pipeline`` background task the
gateway-dispatched envelopes use.

Today's scope (W4-P3 text-only)
-------------------------------
* Inbound text → ``command`` kind → supervisor.
* Inbound media → 200 OK + log + drop. Twilio MediaURL fetch needs the
  firm's auth token and is not wired today; the existing
  ``/worker/document`` route expects Telegram file_ids and would crash.

How CAs map to firms
--------------------
We synthesize ``chat_id`` from the inbound E.164 phone digits
(``+918178803301`` → ``918178803301``). The existing
``find_firm_by_admin_chat_id`` query is reused — operators bind a
beta WA firm by setting ``ca_firms.admin_chat_id`` to the synthesized
int + setting ``ca_firms.primary_channel = 'whatsapp'``.

Signature verification
----------------------
Twilio's HMAC-SHA1 over ``url + sortedParams`` against the account
auth token. Off by default for sandbox use; flip
``WHATSAPP_VERIFY_SIGNATURE=true`` once the production sender is
provisioned.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from base64 import b64encode
from datetime import UTC, datetime
from typing import Any, Final
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import PlainTextResponse

from app.api.middleware.envelope_verify import DecodedEnvelope
from app.logging_setup import get_logger
from app.utils.background import get_registry

__all__ = ["router"]

router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])
_log = get_logger(__name__)

_WHATSAPP_PREFIX: Final[str] = "whatsapp:"
_MAX_BODY_BYTES: Final[int] = 64 * 1024


def _strip_whatsapp_prefix(raw: str) -> str:
    return raw[len(_WHATSAPP_PREFIX):] if raw.startswith(_WHATSAPP_PREFIX) else raw


def _chat_id_from_phone(phone_e164: str) -> int:
    """``+918178803301`` → ``918178803301``."""
    digits = "".join(c for c in phone_e164 if c.isdigit())
    if not digits:
        raise ValueError(f"cannot derive chat_id from {phone_e164!r}")
    return int(digits)


def _hash_sid_to_message_id(sid: str) -> int:
    """FNV-1a 32-bit, sign-bit cleared. Stable per-MessageSid int for logs."""
    h = 0x811C9DC5
    for ch in sid:
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h >> 1


def _build_signature_base(url: str, params: dict[str, str]) -> str:
    base = url
    for k in sorted(params.keys()):
        base += k + params[k]
    return base


def _verify_twilio_signature(
    *, auth_token: str, url: str, params: dict[str, str], header: str | None
) -> bool:
    if not auth_token or not header:
        return False
    base = _build_signature_base(url, params).encode("utf-8")
    expected = b64encode(
        hmac.new(auth_token.encode("utf-8"), base, hashlib.sha1).digest()  # noqa: S324
    ).decode("ascii")
    return hmac.compare_digest(expected, header)


def _reconstruct_public_url(request: Request) -> str:
    """Trust XFP / XFH when the ALB has them set; otherwise use request scope."""
    headers = request.headers
    proto = headers.get("x-forwarded-proto") or request.url.scheme
    host = headers.get("x-forwarded-host") or headers.get("host") or request.url.netloc
    return f"{proto}://{host}{request.url.path}"


def _is_text_command(params: dict[str, str]) -> bool:
    """True iff the message is text we should dispatch as ``command``."""
    body = (params.get("Body") or "").strip()
    return bool(body)


def _build_envelope(params: dict[str, str], from_phone: str) -> DecodedEnvelope:
    """Construct the same envelope shape gateway-dispatched commands carry."""
    text = (params.get("Body") or "").strip()
    sid = params.get("MessageSid") or ""
    message_id = _hash_sid_to_message_id(sid) if sid else None
    return DecodedEnvelope(
        v=1,
        kind="command",
        issued_at=datetime.now(UTC),
        nonce=sid or str(uuid4()),
        chat_id=_chat_id_from_phone(from_phone),
        message_id=message_id,
        update_id=message_id,
        payload={
            "channel": "whatsapp",
            "from_phone": from_phone,
            "message_sid": sid,
            "text": text,
            "entities": [],
        },
    )


@router.post(
    "/twilio",
    summary="Twilio WhatsApp inbound webhook (W4-P3)",
    response_class=PlainTextResponse,
)
async def whatsapp_twilio_webhook(request: Request) -> PlainTextResponse:
    """Accept a Twilio WA POST, spawn the command pipeline, ACK with 200.

    Twilio retries on non-2xx, so we always return 200 even for ignored
    media-only messages. Hard failures (signature mismatch, bad ``From``)
    raise 400/403 — Twilio will retry once and then surface in the
    Console, which is the correct visibility for an attacker probe or
    misconfiguration.
    """
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    verify = os.environ.get("WHATSAPP_VERIFY_SIGNATURE", "false").lower() == "true"

    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="body too large",
        )

    form = await request.form()
    # FastAPI's form parser may return ``UploadFile`` for file fields; Twilio
    # only sends strings, so coerce defensively.
    params: dict[str, str] = {
        k: v for k, v in form.items() if isinstance(v, str)
    }

    if verify:
        url = _reconstruct_public_url(request)
        if not _verify_twilio_signature(
            auth_token=auth_token,
            url=url,
            params=params,
            header=request.headers.get("x-twilio-signature"),
        ):
            _log.warning(
                "wa_webhook_bad_signature",
                url=url,
                message_sid=params.get("MessageSid"),
                from_=params.get("From"),
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    from_raw = params.get("From") or ""
    if not from_raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing From",
        )
    from_phone = _strip_whatsapp_prefix(from_raw)

    if not _is_text_command(params):
        # Media-only or empty body — accept and drop. The supervisor cannot
        # yet consume Twilio MediaURLs (separate pipeline from the Telegram
        # file_id path).
        _log.info(
            "wa_webhook_text_only_today",
            from_=from_raw,
            message_sid=params.get("MessageSid"),
            num_media=params.get("NumMedia"),
        )
        return PlainTextResponse("", status_code=status.HTTP_200_OK)

    try:
        envelope = _build_envelope(params, from_phone)
    except ValueError as exc:
        _log.warning(
            "wa_webhook_envelope_build_failed",
            error=str(exc),
            from_=from_raw,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # Reuse the existing worker pipeline. Lazy import to avoid a cycle —
    # worker.py imports nothing from this module, but importing it at
    # module load would trip the application start-up ordering.
    from app.api.routes.worker import _run_command_pipeline

    registry = get_registry()
    registry.spawn(
        _run_command_pipeline(envelope),
        name=f"wa_pipeline:command:{envelope.message_id or 'none'}",
        context={
            "kind": "command",
            "channel": "whatsapp",
            "chat_id": envelope.chat_id,
            "message_id": envelope.message_id,
        },
    )

    _log.info(
        "wa_webhook_accepted",
        from_=from_raw,
        chat_id=envelope.chat_id,
        message_sid=params.get("MessageSid"),
    )
    return PlainTextResponse("", status_code=status.HTTP_200_OK)
