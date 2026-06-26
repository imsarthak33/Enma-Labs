"""Tests for the CA firm onboarding module.

Exercises the in-memory state machine and handler functions without
standing up a real database — we mock the SQLAlchemy session and the
firm lookup query.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from app.agents.onboarding import (
    OnboardingStage,
    OnboardingState,
    clear_onboarding_state,
    get_onboarding_state,
    handle_onboarding_reply,
    handle_start,
    is_in_onboarding,
    set_onboarding_state,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CHAT_ID = 5487193271
OTHER_CHAT_ID = 9999999999


def _fake_firm(
    firm_name: str = "Test & Co",
    admin_chat_id: int = CHAT_ID,
) -> SimpleNamespace:
    """Minimal object duck-typing a CaFirm row."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        firm_name=firm_name,
        admin_chat_id=admin_chat_id,
    )


def _mock_session() -> AsyncMock:
    """Return a mock AsyncSession with flush and commit."""
    session = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.add = lambda obj: None  # no-op
    return session


@pytest.fixture(autouse=True)
def _clear_state():
    """Ensure onboarding state is clean between tests."""
    clear_onboarding_state(CHAT_ID)
    clear_onboarding_state(OTHER_CHAT_ID)
    yield
    clear_onboarding_state(CHAT_ID)
    clear_onboarding_state(OTHER_CHAT_ID)


# ---------------------------------------------------------------------------
# State machine unit tests
# ---------------------------------------------------------------------------


class TestStateMachine:
    def test_no_state_by_default(self) -> None:
        assert get_onboarding_state(CHAT_ID) is None
        assert not is_in_onboarding(CHAT_ID)

    def test_set_and_get(self) -> None:
        state = OnboardingState(
            chat_id=CHAT_ID,
            stage=OnboardingStage.AWAITING_FIRM_NAME,
        )
        set_onboarding_state(state)
        assert is_in_onboarding(CHAT_ID)

        retrieved = get_onboarding_state(CHAT_ID)
        assert retrieved is not None
        assert retrieved.stage == OnboardingStage.AWAITING_FIRM_NAME

    def test_clear(self) -> None:
        set_onboarding_state(
            OnboardingState(
                chat_id=CHAT_ID,
                stage=OnboardingStage.AWAITING_FIRM_NAME,
            )
        )
        clear_onboarding_state(CHAT_ID)
        assert get_onboarding_state(CHAT_ID) is None

    def test_expired_state_returns_none(self) -> None:
        state = OnboardingState(
            chat_id=CHAT_ID,
            stage=OnboardingStage.AWAITING_FIRM_NAME,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        set_onboarding_state(state)
        assert get_onboarding_state(CHAT_ID) is None
        assert not is_in_onboarding(CHAT_ID)

    def test_independent_chat_ids(self) -> None:
        """Two users onboarding concurrently have independent states."""
        set_onboarding_state(
            OnboardingState(
                chat_id=CHAT_ID,
                stage=OnboardingStage.AWAITING_FIRM_NAME,
            )
        )
        set_onboarding_state(
            OnboardingState(
                chat_id=OTHER_CHAT_ID,
                stage=OnboardingStage.FIRM_CREATED,
            )
        )
        s1 = get_onboarding_state(CHAT_ID)
        s2 = get_onboarding_state(OTHER_CHAT_ID)
        assert s1 is not None and s1.stage == OnboardingStage.AWAITING_FIRM_NAME
        assert s2 is not None and s2.stage == OnboardingStage.FIRM_CREATED


# ---------------------------------------------------------------------------
# /start handler tests
# ---------------------------------------------------------------------------


class TestHandleStart:
    @pytest.mark.asyncio
    async def test_new_user_gets_welcome(self) -> None:
        """A fresh user typing bare /start is sent to web onboarding (web-first)."""
        session = _mock_session()

        with patch(
            "app.agents.onboarding.find_firm_by_admin_chat_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            html = await handle_start(session, CHAT_ID)

        assert "Welcome to Enma" in html
        assert "complete signup on the web" in html
        # Web-first: bare /start does not start a Telegram onboarding flow.
        assert get_onboarding_state(CHAT_ID) is None

    @pytest.mark.asyncio
    async def test_existing_user_gets_welcome_back(self) -> None:
        """A registered user typing /start sees 'Welcome back'."""
        session = _mock_session()
        firm = _fake_firm()

        with patch(
            "app.agents.onboarding.find_firm_by_admin_chat_id",
            new_callable=AsyncMock,
            return_value=firm,
        ):
            html = await handle_start(session, CHAT_ID)

        assert "Welcome back" in html
        assert "Test &amp; Co" in html  # HTML-escaped
        # No onboarding state should be set
        assert get_onboarding_state(CHAT_ID) is None

    @pytest.mark.asyncio
    async def test_start_clears_stale_onboarding_state(self) -> None:
        """Typing /start clears any stale legacy Telegram-onboarding state."""
        # Pre-set a stale state
        set_onboarding_state(
            OnboardingState(
                chat_id=CHAT_ID,
                stage=OnboardingStage.FIRM_CREATED,
                firm_id=uuid.uuid4(),
            )
        )
        session = _mock_session()

        with patch(
            "app.agents.onboarding.find_firm_by_admin_chat_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            html = await handle_start(session, CHAT_ID)

        assert "Welcome to Enma" in html
        # /start is a fresh entry — the stale state is cleared, not advanced.
        assert get_onboarding_state(CHAT_ID) is None

    @pytest.mark.asyncio
    async def test_client_deep_link_binds_chat(self) -> None:
        """A client tapping their deep link binds this 1:1 chat to the client."""
        session = _mock_session()
        cid = uuid.uuid4()
        client = SimpleNamespace(id=cid, trade_name="S.S Traders", telegram_chat_id=None)
        firm = SimpleNamespace(id=uuid.uuid4(), firm_name="Sarthak & Co")

        with patch(
            "app.agents.onboarding.find_client_with_firm",
            new_callable=AsyncMock,
            return_value=(client, firm),
        ):
            html = await handle_start(session, CHAT_ID, f"client_{cid}")

        assert client.telegram_chat_id == CHAT_ID  # bound
        assert "connected to Enma" in html
        assert "S.S Traders" in html
        assert "Sarthak &amp; Co" in html  # firm name HTML-escaped (single-escape)

    @pytest.mark.asyncio
    async def test_invalid_client_link_is_rejected(self) -> None:
        session = _mock_session()
        cid = uuid.uuid4()
        with patch(
            "app.agents.onboarding.find_client_with_firm",
            new_callable=AsyncMock,
            return_value=None,
        ):
            html = await handle_start(session, CHAT_ID, f"client_{cid}")
        assert "no longer valid" in html


# ---------------------------------------------------------------------------
# Onboarding reply handler tests
# ---------------------------------------------------------------------------


class TestHandleOnboardingReply:
    @pytest.mark.asyncio
    async def test_no_state_returns_none(self) -> None:
        """If not in onboarding, return None (pass through to normal pipeline)."""
        session = _mock_session()
        result = await handle_onboarding_reply(session, CHAT_ID, "hello")
        assert result is None

    @pytest.mark.asyncio
    async def test_firm_name_creates_firm(self) -> None:
        """Providing a firm name creates the firm and advances to FIRM_CREATED."""
        set_onboarding_state(
            OnboardingState(
                chat_id=CHAT_ID,
                stage=OnboardingStage.AWAITING_FIRM_NAME,
            )
        )
        session = _mock_session()
        fake_firm = _fake_firm(firm_name="S.S. Associates")

        with patch(
            "app.agents.onboarding.create_firm_with_admin",
            new_callable=AsyncMock,
            return_value=fake_firm,
        ):
            html = await handle_onboarding_reply(
                session, CHAT_ID, "S.S. Associates"
            )

        assert html is not None
        assert "S.S. Associates" in html
        assert "registered" in html

        state = get_onboarding_state(CHAT_ID)
        assert state is not None
        assert state.stage == OnboardingStage.FIRM_CREATED

    @pytest.mark.asyncio
    async def test_too_short_name_rejected(self) -> None:
        """A 1-char name gets rejected with a helpful message."""
        set_onboarding_state(
            OnboardingState(
                chat_id=CHAT_ID,
                stage=OnboardingStage.AWAITING_FIRM_NAME,
            )
        )
        session = _mock_session()
        html = await handle_onboarding_reply(session, CHAT_ID, "A")
        assert html is not None
        assert "too short" in html
        # State should still be AWAITING_FIRM_NAME
        state = get_onboarding_state(CHAT_ID)
        assert state is not None
        assert state.stage == OnboardingStage.AWAITING_FIRM_NAME

    @pytest.mark.asyncio
    async def test_slash_command_during_onboarding_aborts(self) -> None:
        """Sending a /command during onboarding clears state and returns None."""
        set_onboarding_state(
            OnboardingState(
                chat_id=CHAT_ID,
                stage=OnboardingStage.AWAITING_FIRM_NAME,
            )
        )
        session = _mock_session()
        result = await handle_onboarding_reply(
            session, CHAT_ID, "/list_clients"
        )
        # None means: not handled, fall through to normal pipeline
        assert result is None
        assert not is_in_onboarding(CHAT_ID)

    @pytest.mark.asyncio
    async def test_valid_gstin_after_firm_creation(self) -> None:
        """After firm creation, a valid GSTIN is acknowledged and onboarding ends."""
        set_onboarding_state(
            OnboardingState(
                chat_id=CHAT_ID,
                stage=OnboardingStage.FIRM_CREATED,
                firm_id=uuid.uuid4(),
                firm_name="Test Firm",
            )
        )
        session = _mock_session()
        # 27AAACR5055K1Z7 is a valid GSTIN format
        html = await handle_onboarding_reply(
            session, CHAT_ID, "27AAACR5055K1Z7"
        )
        assert html is not None
        assert "GSTIN noted" in html
        assert "27AAACR5055K1Z7" in html
        assert not is_in_onboarding(CHAT_ID)

    @pytest.mark.asyncio
    async def test_non_gstin_after_firm_creation_falls_through(self) -> None:
        """After firm creation, a non-GSTIN message ends onboarding and falls through."""
        set_onboarding_state(
            OnboardingState(
                chat_id=CHAT_ID,
                stage=OnboardingStage.FIRM_CREATED,
                firm_id=uuid.uuid4(),
                firm_name="Test Firm",
            )
        )
        session = _mock_session()
        result = await handle_onboarding_reply(
            session, CHAT_ID, "hello can you help me?"
        )
        # None means: fall through to normal pipeline
        assert result is None
        assert not is_in_onboarding(CHAT_ID)
