"""CA Firm onboarding — conversational /start flow.

Manages a lightweight in-memory state machine for first-time users.
State auto-expires after 30 minutes. No database persistence needed —
if the bot restarts mid-onboarding, the user simply types /start again.

States
------
    AWAITING_FIRM_NAME  — user sent /start, we asked for firm name
    FIRM_CREATED        — firm row inserted, optionally awaiting GSTIN

Why in-memory
-------------
A DB table for onboarding state would require an Alembic migration, a
cleanup cron, and transactional complexity for what is a 2-message flow.
The in-memory dict with TTL is the right trade-off. This matches the
proven KARO PITCH pattern (``_recent_onboards``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.queries.firms import create_firm_with_admin, find_firm_by_admin_chat_id
from app.formatting.telegram_html import bold, code, italic, safe_text
from app.logging_setup import get_logger
from app.tax.gstin_validator import is_valid_gstin

_log = get_logger(__name__)

ONBOARDING_TTL: Final[timedelta] = timedelta(minutes=30)


# ---------------------------------------------------------------------------
# State machine
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


# In-memory state store.  Keys: chat_id → OnboardingState
_states: dict[int, OnboardingState] = {}


def get_onboarding_state(chat_id: int) -> OnboardingState | None:
    """Return active state for ``chat_id``, or ``None`` if absent/expired."""
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
    """Quick predicate for the worker pipeline."""
    return get_onboarding_state(chat_id) is not None


# ---------------------------------------------------------------------------
# /start handler
# ---------------------------------------------------------------------------


async def handle_start(session: AsyncSession, chat_id: int) -> str:
    """Handle ``/start`` — check if already registered, else begin onboarding."""
    existing = await find_firm_by_admin_chat_id(session, chat_id)
    if existing is not None:
        return (
            bold("Welcome back!") + "\n"
            "Your firm " + bold(existing.firm_name)
            + " is already registered.\n\n"
            + "Commands:\n"
            + "• " + code("/add_client") + " — Add a new client\n"
            + "• " + code("/list_clients") + " — View your clients\n"
            + "• " + code('/status "Client Name"') + " — Check client status\n"
            + "• Send me any GST invoice to process it"
        )

    set_onboarding_state(
        OnboardingState(
            chat_id=chat_id,
            stage=OnboardingStage.AWAITING_FIRM_NAME,
        )
    )

    return (
        bold("Welcome to Enma!") + " 🎉\n\n"
        + "I'm your AI-powered GST compliance assistant for "
        + "Chartered Accountant firms.\n\n"
        + bold("To get started, what is your CA firm's name?") + "\n\n"
        + italic("Example: S.S. Associates, Kumar & Partners")
    )


# ---------------------------------------------------------------------------
# Mid-onboarding reply handler
# ---------------------------------------------------------------------------


async def handle_onboarding_reply(
    session: AsyncSession,
    chat_id: int,
    text: str,
) -> str | None:
    """Handle a message from a user who is mid-onboarding.

    Returns the reply HTML, or ``None`` if the message is not an
    onboarding reply (meaning it should fall through to the normal
    pipeline — onboarding state is cleared internally in that case).
    """
    state = get_onboarding_state(chat_id)
    if state is None:
        return None

    if state.stage == OnboardingStage.AWAITING_FIRM_NAME:
        return await _handle_firm_name(session, chat_id, text, state)

    if state.stage == OnboardingStage.FIRM_CREATED:
        return await _handle_post_creation(session, chat_id, text, state)

    return None  # pragma: no cover — unreachable unless we add stages


# ---------------------------------------------------------------------------
# Internal stage handlers
# ---------------------------------------------------------------------------


async def _handle_firm_name(
    session: AsyncSession,
    chat_id: int,
    text: str,
    state: OnboardingState,
) -> str | None:
    """User provided their firm name.  Create the firm row."""
    name = text.strip()

    # If the user sent a slash command mid-onboarding, abort and let the
    # normal command pipeline handle it.
    if name.startswith("/"):
        clear_onboarding_state(chat_id)
        return None

    # Basic validation
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

    # Advance state
    state.stage = OnboardingStage.FIRM_CREATED
    state.firm_id = firm.id
    state.firm_name = name
    set_onboarding_state(state)

    _log.info(
        "firm_onboarded",
        firm_id=str(firm.id),
        firm_name=name,
        chat_id=chat_id,
    )

    return (
        "✅ " + bold(safe_text(name)) + " has been registered!\n\n"
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
    """After firm creation, optionally catch GSTIN or end onboarding."""
    stripped = text.strip()

    # Slash command → end onboarding, fall through to normal pipeline
    if stripped.startswith("/"):
        clear_onboarding_state(chat_id)
        return None

    # Check if the message looks like a GSTIN
    candidate = stripped.upper().replace(" ", "")
    if is_valid_gstin(candidate):
        # NOTE: The CaFirm model does not currently have a firm-level GSTIN
        # column. We acknowledge it and complete onboarding. A migration to
        # add ``firm_gstin`` can be added later if needed.
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

    # Not a GSTIN — probably a regular message.  End onboarding, pass through.
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
