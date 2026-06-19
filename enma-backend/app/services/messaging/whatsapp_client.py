"""WhatsApp implementation of :class:`MessageClient` via Twilio.

Why Twilio for MVP
------------------
Per the W4 design conversation: Meta Cloud direct gives lower
per-message cost but requires WABA verification + phone provisioning
(1-2 weeks of review per firm). Twilio's WhatsApp sandbox spins up
in 5 minutes and the production swap to Meta later is contained to
this one file. Same ``MessageClient`` surface — call sites never know.

Wire shape
----------
Twilio's Programmable Messaging API speaks the v2010-04-01 form:

    POST https://api.twilio.com/2010-04-01/Accounts/{Sid}/Messages.json
    Authorization: Basic base64({Sid}:{AuthToken})
    Content-Type: application/x-www-form-urlencoded

    From=whatsapp%3A%2B14155238886
    To=whatsapp%3A%2B919876543210
    Body=Hello%20from%20Enma

Both ``From`` and ``To`` MUST be prefixed with ``whatsapp:`` and use
E.164 format. We do that here so call sites just pass the recipient
phone number unmodified.

Document delivery (P2.b.2 — not yet wired)
------------------------------------------
WhatsApp media sends need a publicly fetchable URL Twilio can pull
from. The cleanest path is presigned S3 (upload bytes to a
short-lived key, hand Twilio the URL). Implementing that requires
boto3 + an S3 prefix policy with 1-day lifecycle expiry. Deferred
until we can validate end-to-end against the sandbox.
"""

from __future__ import annotations

from base64 import b64encode
from typing import Any, Final

import httpx

from app.services.messaging.base import Markup, MessageClient, MessagingError
from app.services.messaging.render import render_whatsapp

__all__ = ["WhatsAppClient"]


_TWILIO_API_BASE: Final[str] = "https://api.twilio.com/2010-04-01"
_DEFAULT_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(
    connect=5.0, read=30.0, write=30.0, pool=5.0
)
_WHATSAPP_PREFIX: Final[str] = "whatsapp:"


class WhatsAppClient(MessageClient):
    """:class:`MessageClient` backed by Twilio's WhatsApp API.

    Constructed once per firm by the factory with the firm's decrypted
    credentials. Holds a process-wide :class:`httpx.AsyncClient` so
    connection pooling carries across sends — important under cron
    bursts (briefings to many firms in one tick).
    """

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        from_number: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not account_sid:
            raise MessagingError("WhatsAppClient: account_sid is required")
        if not auth_token:
            raise MessagingError("WhatsAppClient: auth_token is required")
        if not from_number:
            raise MessagingError("WhatsAppClient: from_number is required")
        self._sid = account_sid
        self._auth_token = auth_token
        self._from = _ensure_whatsapp_prefix(from_number)
        self._http = http_client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
        token = f"{account_sid}:{auth_token}".encode()
        self._auth_header = "Basic " + b64encode(token).decode("ascii")

    # ------------------------------------------------------------------
    # MessageClient surface
    # ------------------------------------------------------------------

    async def send_message(
        self,
        *,
        recipient: int | str,
        body: Markup,
        reply_to_id: int | str | None = None,  # — Twilio doesn't surface threading
        disable_notification: bool = False,  # — WA has no quiet mode
    ) -> dict[str, Any]:
        to = _ensure_whatsapp_prefix(_coerce_phone(recipient))
        text = render_whatsapp(body)
        if not text:
            raise MessagingError("WhatsAppClient.send_message: empty body")

        url = f"{_TWILIO_API_BASE}/Accounts/{self._sid}/Messages.json"
        form = {"From": self._from, "To": to, "Body": text}
        return await self._post_form(url, form, label="send_message")

    async def send_document(
        self,
        *,
        recipient: int | str,  # — surface stable; impl pending
        file_bytes: bytes,
        filename: str,
        caption: Markup | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "WhatsApp send_document is staged for P2.b.2 — needs S3 "
            "presigned-URL upload before Twilio can fetch the media. "
            "Today, fall back to a text reply with a link to the file."
        )

    async def download_file(self, file_ref: str) -> bytes:
        """Fetch a Twilio-hosted media URL with Basic auth.

        Inbound webhooks deliver ``MediaUrl{N}`` parameters that point
        at ``https://api.twilio.com/.../Media/{Sid}``. Twilio gates
        access behind the account's Basic auth — same credentials as
        send.
        """
        if not file_ref or not file_ref.startswith("https://"):
            raise MessagingError(
                f"WhatsAppClient.download_file: expected an https Twilio "
                f"Media URL, got {file_ref!r}"
            )
        try:
            resp = await self._http.get(
                file_ref,
                headers={"Authorization": self._auth_header},
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise MessagingError(
                f"WhatsAppClient.download_file transport error: {exc}"
            ) from exc
        if resp.status_code != httpx.codes.OK:
            raise MessagingError(
                f"WhatsAppClient.download_file HTTP {resp.status_code}: "
                f"{resp.text[:200]}"
            )
        return resp.content

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _post_form(
        self, url: str, form: dict[str, str], *, label: str
    ) -> dict[str, Any]:
        try:
            resp = await self._http.post(
                url,
                data=form,
                headers={"Authorization": self._auth_header},
            )
        except httpx.HTTPError as exc:
            raise MessagingError(f"twilio {label} transport error: {exc}") from exc
        if resp.status_code >= httpx.codes.BAD_REQUEST:
            raise MessagingError(
                f"twilio {label} HTTP {resp.status_code}: {resp.text[:300]}"
            )
        try:
            body: dict[str, Any] = resp.json()
        except ValueError as exc:
            raise MessagingError(
                f"twilio {label} non-JSON response: {resp.text[:300]}"
            ) from exc
        return body


def _ensure_whatsapp_prefix(number: str) -> str:
    """Prepend ``whatsapp:`` if the caller passed a bare E.164 phone."""
    return number if number.startswith(_WHATSAPP_PREFIX) else f"{_WHATSAPP_PREFIX}{number}"


def _coerce_phone(recipient: int | str) -> str:
    """WhatsApp recipients must be E.164 strings — reject ints loudly."""
    if isinstance(recipient, int):
        raise MessagingError(
            f"WhatsAppClient: recipient must be an E.164 phone string "
            f"(e.g. '+919876543210'), got int {recipient!r} — that's "
            f"a Telegram chat_id slipped through the factory."
        )
    if not recipient:
        raise MessagingError("WhatsAppClient: recipient is required")
    return recipient
