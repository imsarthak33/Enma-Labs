"""CA Firm onboarding — Telegram side of the web onboarding handoff.

Canonical flow (Phase 9+):
    1. User signs up on the web app (Next.js + Supabase auth).
    2. Frontend inserts a ``ca_firms`` row with their profile, consents,
       and ``supabase_user_id``.
    3. Dashboard generates a deep-link ``t.me/<bot>?start=<firm_uuid>``.
    4. User taps it → Telegram sends ``/start <firm_uuid>``.
    5. We look the row up by id, write ``admin_chat_id`` +
       ``telegram_chat_id`` + ``telegram_linked_at``, and welcome them.

Fallback flow (legacy / users who haven't signed up):
    A bare ``/start`` from an unknown chat doesn't try to onboard via
    Telegram any more — the web form collects consents, password and
    profile that the bot can't reliably gather. We point them at the
    web instead. The conversational state machine is still defined and
    exported so existing tests pass, but it's only reachable if a future
    feature wants it back.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.queries.firms import (
    create_firm_with_admin,
    find_firm_by_admin_chat_id,
    get_firm_by_id,
    link_chat_to_firm,
)
from app.formatting.telegram_html import bold, code, italic, safe_text
from app.logging_setup import get_logger
from app.tax.gstin_validator import is_valid_gstin

_log = get_logger(__name__)

ONBOARDING_TTL: Final[timedelta] = timedelta(minutes=30)

# Frontend URL — shown to legacy users so they can complete signup.
# Override via env if the marketing site moves; falls back to a sensible
# default so the bot never sends an empty link.
_DEFAULT_WEB_ONBOARDING_URL: Final[str] = "https://enmalabs.in/onboarding"


def _web_onboarding_url() -> str:
    url = getattr(settings, "web_onboarding_url", None)
    if isinstance(url, str) and url.strip():
        return url.strip()
    return _DEFAULT_WEB_ONBOARDING_URL


# ---------------------------------------------------------------------------
# In-memory conversational state (fallback path only)
# ---------------------------------------------------------------------------


class OnboardingStage(str, Enum):
    AWAITING_FIRM_NAME = "awaiting_firm_name"
    FIRM_CREATED = "firm_created"


@dataclass
class OnboardingState:
    """Ephemeral onboarding state for a single chat_id."""

    chat_id: int
    stage: OnboardingStage
    firm_id: uuid.UUID | None = None
    firm_name: str | None = None
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(UTC) + ONBOARDING_TTL
    )

    @property
    def is_expired(self) -> bool:
        return datetime.now(UTC) > self.expires_at


_states: dict[int, OnboardingState] = {}


def get_onboarding_state(chat_id: int) -> OnboardingState | None:
    state = _states.get(chat_id)
    if state is None:
        return None
    if state.is_expired:
        _states.pop(chat_id, None)
        return None
    return state


def set_onboarding_state(state: OnboardingState) -> None:
    _states[state.chat_id] = state


def clear_onboarding_state(chat_id: int) -> None:
    _states.pop(chat_id, None)


def is_in_onboarding(chat_id: int) -> bool:
    return get_onboarding_state(chat_id) is not None


# ---------------------------------------------------------------------------
# /start handler — UUID deep-link first, web redirect fallback
# ---------------------------------------------------------------------------


async def handle_start(
    session: AsyncSession,
    chat_id: int,
    payload: str | None = None,
) -> str:
    """Handle ``/start`` (with or without a deep-link payload).

    Order of precedence:
      1. ``/start <uuid>`` → look the firm up; if it exists and is
         unlinked, bind this chat to it.
      2. ``/start <uuid>`` for an already-linked firm → "welcome back".
      3. Bare ``/start`` from a chat that's already linked → "welcome
         back".
      4. Bare ``/start`` from an unknown chat → send the user to the
         web onboarding (we do NOT collect consents over Telegram).

    ``/start`` is always a fresh entry point, so it first clears any stale
    legacy Telegram-onboarding state for this chat (onboarding is web-first).
    """
    clear_onboarding_state(chat_id)

    # ── 1 & 2: payload-driven link ─────────────────────────────────────────
    if payload:
        firm = await _resolve_firm_payload(session, payload)
        if firm is not None:
            # Already linked to this same chat? Idempotent welcome-back.
            if firm.admin_chat_id == chat_id:
                return _welcome_back_html(firm)

            # Linked to a *different* chat — refuse to silently steal it.
            if firm.admin_chat_id is not None and firm.admin_chat_id != chat_id:
                _log.warning(
                    "firm_link_conflict",
                    firm_id=str(firm.id),
                    existing_chat_id=firm.admin_chat_id,
                    requesting_chat_id=chat_id,
                )
                return (
                    italic("This firm is already linked to a different Telegram account.")
                    + "\n\n"
                    + "If you've changed phones, contact your admin to "
                    + "re-link from the web dashboard."
                )

            # Bind this chat to the firm.
            await link_chat_to_firm(
                session,
                firm=firm,
                chat_id=chat_id,
                telegram_bot_token=settings.telegram_bot_token.get_secret_value(),
            )
            await session.commit()

            _log.info(
                "firm_linked_from_frontend",
                firm_id=str(firm.id),
                firm_name=firm.firm_name,
                chat_id=chat_id,
            )
            return _link_success_html(firm)

        # Payload was syntactically a UUID but resolved to nothing — fall
        # through to the unknown-chat path. (Quiet: no error to the user;
        # we just behave as if no payload was sent.)
        _log.info("firm_link_payload_unresolved", payload=payload, chat_id=chat_id)

    # ── 3: bare /start from a chat we already know ─────────────────────────
    existing = await find_firm_by_admin_chat_id(session, chat_id)
    if existing is not None:
        return _welcome_back_html(existing)

    # ── 4: unknown chat, no payload → web redirect ─────────────────────────
    return _web_redirect_html()


async def _resolve_firm_payload(session: AsyncSession, payload: str):  # type: ignore[no-untyped-def]
    """Validate the payload as a UUID and resolve it, or return None."""
    try:
        firm_id = uuid.UUID(payload)
    except ValueError:
        return None
    return await get_firm_by_id(session, firm_id)


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------


def _link_success_html(firm) -> str:  # type: ignore[no-untyped-def]
    name_part = bold(firm.firm_name)
    salutation = (
        "Welcome, " + bold(firm.ca_name) + "!\n\n"
        if firm.ca_name
        else bold("Welcome to Enma!") + " 🎉\n\n"
    )
    return (
        salutation
        + "Your Telegram is now linked to " + name_part + ".\n\n"
        + bold("You're all set.") + " Here's what I can do:\n"
        + "• " + code('/add_client "Client Name"') + " — Add your first client\n"
        + "• " + code("/list_clients") + " — View your clients\n"
        + "• " + code('/status "Client Name"') + " — Check client status\n"
        + "• Send me any GST invoice to process it automatically"
    )


def _welcome_back_html(firm) -> str:  # type: ignore[no-untyped-def]
    return (
        bold("Welcome back!") + "\n"
        "Your firm " + bold(firm.firm_name)
        + " is already linked.\n\n"
        + "Commands:\n"
        + "• " + code("/add_client") + " — Add a new client\n"
        + "• " + code("/list_clients") + " — View your clients\n"
        + "• " + code('/status "Client Name"') + " — Check client status\n"
        + "• Send me any GST invoice to process it"
    )


def _web_redirect_html() -> str:
    return (
        bold("Welcome to Enma!") + " 🎉\n\n"
        + "I'm your AI-powered GST compliance assistant for "
        + "Chartered Accountant firms.\n\n"
        + bold("Please complete signup on the web first:") + "\n"
        + safe_text(_web_onboarding_url()) + "\n\n"
        + italic(
            "We need your DPA and privacy consents before any data flows — "
            "that's a 60-second form. After you finish, you'll get a "
            "personalised link back to this bot."
        )
    )


# ---------------------------------------------------------------------------
# Mid-onboarding reply handler (legacy fallback)
# ---------------------------------------------------------------------------


async def handle_onboarding_reply(
    session: AsyncSession,
    chat_id: int,
    text: str,
) -> str | None:
    """Handle a message from a user mid-onboarding.

    Returns the reply HTML, or ``None`` if the message is not an
    onboarding reply (so the worker pipeline falls through to its normal
    handling). The web flow is the primary path; this remains so legacy
    state machines that still exist in memory don't strand users.
    """
    state = get_onboarding_state(chat_id)
    if state is None:
        return None

    if state.stage == OnboardingStage.AWAITING_FIRM_NAME:
        return await _handle_firm_name(session, chat_id, text, state)

    if state.stage == OnboardingStage.FIRM_CREATED:
        return await _handle_post_creation(session, chat_id, text, state)

    return None  # pragma: no cover — unreachable unless we add stages


async def _handle_firm_name(
    session: AsyncSession,
    chat_id: int,
    text: str,
    state: OnboardingState,
) -> str | None:
    name = text.strip()

    if name.startswith("/"):
        clear_onboarding_state(chat_id)
        return None

    if len(name) < 2:
        return italic("That seems too short. Please enter your firm's full name.")
    if len(name) > 200:
        return italic("That's too long — please use a shorter firm name.")

    bot_token = settings.telegram_bot_token.get_secret_value()
    firm = await create_firm_with_admin(
        session,
        firm_name=name,
        admin_chat_id=chat_id,
        telegram_bot_token=bot_token,
    )
    await session.commit()

    state.stage = OnboardingStage.FIRM_CREATED
    state.firm_id = firm.id
    state.firm_name = name
    set_onboarding_state(state)

    _log.info(
        "firm_onboarded_legacy",
        firm_id=str(firm.id),
        firm_name=name,
        chat_id=chat_id,
    )

    return (
        "✅ " + bold(name) + " has been registered!\n\n"
        + italic("Optional:") + " Send me your firm's GSTIN for enhanced "
        + "verification, or skip this step.\n\n"
        + "You can also start right away:\n"
        + "• " + code('/add_client "Client Name"') + " — Add your first client\n"
        + "• Send me a GST invoice to process"
    )


async def _handle_post_creation(
    session: AsyncSession,
    chat_id: int,
    text: str,
    state: OnboardingState,
) -> str | None:
    stripped = text.strip()

    if stripped.startswith("/"):
        clear_onboarding_state(chat_id)
        return None

    candidate = stripped.upper().replace(" ", "")
    if is_valid_gstin(candidate):
        clear_onboarding_state(chat_id)
        _log.info(
            "firm_gstin_provided",
            firm_id=str(state.firm_id),
            chat_id=chat_id,
        )
        return (
            "✅ Firm GSTIN noted: " + code(candidate) + "\n\n"
            + bold("You're all set!") + " Here's what I can do:\n"
            + "• " + code("/add_client") + " — Add a client\n"
            + "• " + code("/list_clients") + " — View your clients\n"
            + "• Send me any GST invoice to process it automatically"
        )

    clear_onboarding_state(chat_id)
    return None


__all__ = [
    "OnboardingStage",
    "OnboardingState",
    "clear_onboarding_state",
    "get_onboarding_state",
    "handle_onboarding_reply",
    "handle_start",
    "is_in_onboarding",
    "set_onboarding_state",
]
