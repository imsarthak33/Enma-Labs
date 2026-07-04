"""Per-client GSP OTP-consent flow (Track-A automation, Phase 7b).

The GSTR-2B pull (7b.2) needs a per-client auth token that only the taxpayer
can authorise, via an OTP GSTN sends to their registered mobile. This module
drives that handshake, CA-relayed over Telegram:

    1. CA runs ``/consent "Client"`` → :func:`start_consent` asks the GSP to
       send the OTP to the client's registered mobile and parks a short-lived
       pending state on the CA's chat.
    2. The CA gets the OTP from their client and replies with it →
       :func:`handle_consent_reply` exchanges it for an auth token and stores
       it on ``clients.gsp_auth_token`` (+ expiry), which the pull cron reads.

Dormant until a GSP is configured: with no credentials
:func:`get_gsp_client` returns the no-op client, so ``/consent`` tells the CA
the feature isn't active yet and no state is created.

State is in-memory + TTL'd (mirrors :mod:`app.agents.onboarding`): the OTP
window is minutes, so surviving a process restart isn't worth a table.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.queries.clients import ClientQuery
from app.formatting.telegram_html import bold, code, italic, safe_text
from app.logging_setup import get_logger
from app.services.providers.gsp import get_gsp_client

__all__ = [
    "CONSENT_TTL",
    "ConsentState",
    "clear_consent_state",
    "handle_consent_reply",
    "is_in_consent",
    "start_consent",
]

_log = get_logger(__name__)

CONSENT_TTL: Final[timedelta] = timedelta(minutes=10)
# A GSTN OTP is 6 digits; accept 4-8 to tolerate GSP variants.
_OTP_RE: Final[re.Pattern[str]] = re.compile(r"^\s*(\d{4,8})\s*$")


@dataclass
class ConsentState:
    """Pending OTP consent for one CA chat."""

    chat_id: int
    ca_firm_id: uuid.UUID
    client_id: uuid.UUID
    client_name: str
    gstin: str
    txn_id: str
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(UTC) + CONSENT_TTL
    )

    @property
    def is_expired(self) -> bool:
        return datetime.now(UTC) > self.expires_at


_states: dict[int, ConsentState] = {}


def _get_state(chat_id: int) -> ConsentState | None:
    state = _states.get(chat_id)
    if state is None:
        return None
    if state.is_expired:
        _states.pop(chat_id, None)
        return None
    return state


def clear_consent_state(chat_id: int) -> None:
    _states.pop(chat_id, None)


def is_in_consent(chat_id: int) -> bool:
    return _get_state(chat_id) is not None


async def start_consent(
    session: AsyncSession,
    *,
    ca_firm_id: uuid.UUID,
    chat_id: int,
    client_name: str,
) -> str:
    """Begin the OTP consent for a client. Returns the CA-facing HTML reply."""
    gsp = get_gsp_client(settings)
    if not gsp.is_configured:
        return italic(
            "GSTR-2B auto-pull isn't switched on yet. Once we've onboarded a "
            "GST Suvidha Provider, you'll be able to authorise clients here."
        )

    clients = ClientQuery(session=session, ca_firm_id=ca_firm_id)
    matches = list(await clients.search_by_name(client_name.strip()))
    if not matches:
        return _fail("No client matched " + code(safe_text(client_name)) + ".")
    if len(matches) > 1:
        listed = "\n".join("• " + safe_text(c.trade_name) for c in matches[:5])
        return _fail(bold("Multiple clients matched") + " — be specific:\n" + listed)
    client = matches[0]
    if not client.gstin:
        return _fail(
            safe_text(client.trade_name) + " has no GSTIN on file. Add it first "
            "with " + code('/add_client') + " (or update the client), then retry."
        )

    challenge = await gsp.request_consent_otp(gstin=client.gstin)
    if challenge is None:
        return _fail(
            "Couldn't reach the GSP to send the OTP. Please try again shortly."
        )

    _states[chat_id] = ConsentState(
        chat_id=chat_id,
        ca_firm_id=ca_firm_id,
        client_id=client.id,
        client_name=client.trade_name,
        gstin=client.gstin,
        txn_id=challenge.txn_id,
    )
    _log.info(
        "consent_otp_requested",
        ca_firm_id=str(ca_firm_id),
        client_id=str(client.id),
        chat_id=chat_id,
    )
    return (
        bold("OTP sent") + " 📲\n\n"
        + "GSTN has texted a one-time code to " + safe_text(client.trade_name)
        + "'s registered mobile (GSTIN " + code(client.gstin) + ").\n\n"
        + "Ask them for it and reply here with the code to authorise the "
        + "monthly GSTR-2B pull.\n"
        + italic(f"The code expires in ~{CONSENT_TTL.seconds // 60} minutes.")
    )


async def handle_consent_reply(
    session: AsyncSession,
    *,
    chat_id: int,
    text: str,
) -> str | None:
    """If the chat is mid-consent and ``text`` is an OTP, verify + store.

    Returns the reply HTML, or ``None`` when the chat isn't awaiting an OTP or
    the message isn't OTP-shaped (so the caller falls through to normal
    handling — e.g. the CA typed a real question instead of the code).
    """
    state = _get_state(chat_id)
    if state is None:
        return None
    match = _OTP_RE.match(text)
    if match is None:
        return None  # not an OTP — let the pipeline handle it normally
    otp = match.group(1)

    gsp = get_gsp_client(settings)
    token = await gsp.verify_consent_otp(
        gstin=state.gstin, txn_id=state.txn_id, otp=otp
    )
    if token is None:
        return _fail(
            "That code didn't verify. Double-check it with your client and "
            "reply again, or re-run " + code('/consent') + " for a fresh OTP."
        )

    clients = ClientQuery(session=session, ca_firm_id=state.ca_firm_id)
    await clients.update(
        state.client_id,
        gsp_auth_token=token.token,
        gsp_auth_token_expires_at=token.expires_at,
    )
    await session.commit()
    clear_consent_state(chat_id)
    _log.info(
        "consent_otp_verified",
        ca_firm_id=str(state.ca_firm_id),
        client_id=str(state.client_id),
        expires_at=token.expires_at.isoformat(),
    )
    return (
        bold("Authorised") + " ✅\n\n"
        + safe_text(state.client_name) + "'s GSTR-2B will now pull "
        + "automatically each month — no more manual downloads.\n"
        + italic("You can re-authorise anytime the consent expires.")
    )


def _fail(html: str) -> str:
    return html
