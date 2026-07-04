"""Tests for the Phase 7b GSP OTP-consent flow.

The GSP client and ClientQuery are faked so we exercise the state machine +
copy without a DB or a real GSP.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from app.agents import consent as consent_mod
from app.agents.consent import (
    clear_consent_state,
    handle_consent_reply,
    is_in_consent,
    start_consent,
)
from app.services.providers.gsp import GspAuthToken, OtpChallenge

CHAT_ID = 700100
FIRM_ID = uuid.uuid4()


class _FakeGsp:
    def __init__(
        self,
        *,
        configured: bool = True,
        challenge: OtpChallenge | None = None,
        token: GspAuthToken | None = None,
    ) -> None:
        self._configured = configured
        self._challenge = challenge or OtpChallenge(txn_id="txn-1")
        self._token = token or GspAuthToken(
            token="auth-xyz", expires_at=datetime.now(UTC) + timedelta(hours=6)
        )
        self.verified_with: dict[str, str] | None = None

    @property
    def is_configured(self) -> bool:
        return self._configured

    async def request_consent_otp(self, *, gstin: str) -> OtpChallenge | None:
        return self._challenge

    async def verify_consent_otp(
        self, *, gstin: str, txn_id: str, otp: str
    ) -> GspAuthToken | None:
        self.verified_with = {"gstin": gstin, "txn": txn_id, "otp": otp}
        return self._token


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    gsp: _FakeGsp,
    clients: list[SimpleNamespace],
) -> dict[str, Any]:
    updates: dict[str, Any] = {}

    class _FakeClientQuery:
        def __init__(self, **_kw: Any) -> None: ...

        async def search_by_name(self, name: str) -> list[SimpleNamespace]:
            return clients

        async def update(self, client_id: Any, **kwargs: Any) -> None:
            updates["client_id"] = client_id
            updates.update(kwargs)

    monkeypatch.setattr(consent_mod, "get_gsp_client", lambda _s: gsp)
    monkeypatch.setattr(consent_mod, "ClientQuery", _FakeClientQuery)
    return updates


def _client(name: str = "S.S Traders", gstin: str | None = "27AABCU9603R1ZN") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), trade_name=name, gstin=gstin)


@pytest.fixture(autouse=True)
def _clean() -> Any:
    clear_consent_state(CHAT_ID)
    yield
    clear_consent_state(CHAT_ID)


async def test_dormant_when_gsp_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, gsp=_FakeGsp(configured=False), clients=[_client()])
    html = await start_consent(
        AsyncMock(), ca_firm_id=FIRM_ID, chat_id=CHAT_ID, client_name="S.S Traders"
    )
    assert "isn't switched on yet" in html
    assert not is_in_consent(CHAT_ID)


async def test_start_requests_otp_and_parks_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, gsp=_FakeGsp(), clients=[_client()])
    html = await start_consent(
        AsyncMock(), ca_firm_id=FIRM_ID, chat_id=CHAT_ID, client_name="S.S Traders"
    )
    assert "OTP sent" in html
    assert is_in_consent(CHAT_ID)


async def test_start_rejects_client_without_gstin(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, gsp=_FakeGsp(), clients=[_client(gstin=None)])
    html = await start_consent(
        AsyncMock(), ca_firm_id=FIRM_ID, chat_id=CHAT_ID, client_name="S.S Traders"
    )
    assert "no GSTIN" in html
    assert not is_in_consent(CHAT_ID)


async def test_otp_reply_verifies_and_stores_token(monkeypatch: pytest.MonkeyPatch) -> None:
    gsp = _FakeGsp()
    client = _client()
    updates = _install(monkeypatch, gsp=gsp, clients=[client])
    session = AsyncMock()

    await start_consent(session, ca_firm_id=FIRM_ID, chat_id=CHAT_ID, client_name="S.S Traders")
    html = await handle_consent_reply(session, chat_id=CHAT_ID, text=" 123456 ")

    assert html is not None and "Authorised" in html
    assert gsp.verified_with == {"gstin": client.gstin, "txn": "txn-1", "otp": "123456"}
    assert updates["client_id"] == client.id
    assert updates["gsp_auth_token"] == "auth-xyz"
    assert "gsp_auth_token_expires_at" in updates
    assert not is_in_consent(CHAT_ID)  # cleared on success


async def test_non_otp_text_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, gsp=_FakeGsp(), clients=[_client()])
    session = AsyncMock()
    await start_consent(session, ca_firm_id=FIRM_ID, chat_id=CHAT_ID, client_name="S.S Traders")
    # A real question, not a code → return None so the pipeline handles it.
    assert await handle_consent_reply(session, chat_id=CHAT_ID, text="what's my status?") is None
    assert is_in_consent(CHAT_ID)  # still awaiting the OTP


async def test_no_pending_state_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, gsp=_FakeGsp(), clients=[_client()])
    assert await handle_consent_reply(AsyncMock(), chat_id=CHAT_ID, text="123456") is None


async def test_bad_otp_keeps_state_for_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    class _RejectingGsp(_FakeGsp):
        async def verify_consent_otp(self, *, gstin: str, txn_id: str, otp: str) -> None:
            return None

    _install(monkeypatch, gsp=_RejectingGsp(), clients=[_client()])
    session = AsyncMock()
    await start_consent(session, ca_firm_id=FIRM_ID, chat_id=CHAT_ID, client_name="S.S Traders")
    html = await handle_consent_reply(session, chat_id=CHAT_ID, text="000000")
    assert html is not None and "didn't verify" in html
    assert is_in_consent(CHAT_ID)  # kept so the CA can retry
