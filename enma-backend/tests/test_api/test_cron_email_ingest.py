"""Tests — the email-ingest poll cron (Phase 7a).

Single-mailbox poll (not a per-firm fan-out): each attachment routes to its
client by the per-client ingest address. DB / email-client mocked.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.api.routes import cron as cron_module
from app.services.providers.email_ingest import FetchedAttachment


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_a: object) -> None:
        return None

    async def commit(self) -> None:
        return None


def _fake_sessionmaker() -> Any:
    @asynccontextmanager
    async def factory() -> Any:
        yield _FakeSession()

    return factory


def _att(client_id: uuid.UUID | None) -> FetchedAttachment:
    rec = (
        (f"client-{client_id}@ingest.enmalabs.in",)
        if client_id is not None
        else ("statements@bank.example",)
    )
    return FetchedAttachment(
        filename="statement.pdf",
        content=b"%PDF fake",
        sender="bank",
        subject="stmt",
        recipients=rec,
    )


def _email_client(*, configured: bool, attachments: list[FetchedAttachment]) -> MagicMock:
    c = MagicMock()
    c.is_configured = configured
    c.fetch_statements = AsyncMock(return_value=attachments)
    return c


async def test_routes_attachment_to_its_client(monkeypatch: Any) -> None:
    cid = uuid.uuid4()
    client = SimpleNamespace(id=cid, trade_name="S.S Traders")
    firm = SimpleNamespace(id=uuid.uuid4(), admin_chat_id=1)
    email_client = _email_client(configured=True, attachments=[_att(cid)])
    ingest = AsyncMock(return_value=SimpleNamespace(ingested=True))

    monkeypatch.setattr(cron_module, "get_email_ingest_client", lambda _s: email_client)
    monkeypatch.setattr(
        cron_module, "find_client_with_firm", AsyncMock(return_value=(client, firm))
    )
    monkeypatch.setattr(cron_module.auto_ingest, "ingest_bank_statement_bytes", ingest)
    monkeypatch.setattr(cron_module, "get_sessionmaker", _fake_sessionmaker)

    await cron_module._run_email_ingest(MagicMock())

    ingest.assert_awaited_once()
    kwargs = ingest.await_args.kwargs
    assert kwargs["client"] is client
    assert kwargs["firm"] is firm
    assert kwargs["file_bytes"] == b"%PDF fake"


async def test_unconfigured_is_noop(monkeypatch: Any) -> None:
    email_client = _email_client(configured=False, attachments=[])
    monkeypatch.setattr(cron_module, "get_email_ingest_client", lambda _s: email_client)

    await cron_module._run_email_ingest(MagicMock())

    email_client.fetch_statements.assert_not_awaited()  # dormant — never polls


async def test_unrouted_attachment_skipped(monkeypatch: Any) -> None:
    email_client = _email_client(configured=True, attachments=[_att(None)])
    find = AsyncMock()
    ingest = AsyncMock()
    monkeypatch.setattr(cron_module, "get_email_ingest_client", lambda _s: email_client)
    monkeypatch.setattr(cron_module, "find_client_with_firm", find)
    monkeypatch.setattr(cron_module.auto_ingest, "ingest_bank_statement_bytes", ingest)
    monkeypatch.setattr(cron_module, "get_sessionmaker", _fake_sessionmaker)

    await cron_module._run_email_ingest(MagicMock())

    find.assert_not_awaited()  # no client_id → never resolved
    ingest.assert_not_awaited()


# ── GSTR-2B monthly pull (Phase 7b.2) ──────────────────────────────────────


async def test_gstr2b_pull_routes_consented_clients(monkeypatch: Any) -> None:
    firm = SimpleNamespace(id=uuid.uuid4(), admin_chat_id=1)
    client = SimpleNamespace(
        id=uuid.uuid4(), gstin="27AABCC1234D1Z5", gsp_auth_token="otp-token"
    )
    gsp = MagicMock()
    gsp.is_configured = True
    gsp.fetch_gstr2b = AsyncMock(return_value=b'{"data": {"docdata": {}}}')
    clients_q = MagicMock()
    clients_q.list_with_gsp_consent = AsyncMock(return_value=[client])
    ingest = AsyncMock(return_value=SimpleNamespace(ingested=True))

    monkeypatch.setattr(cron_module, "get_gsp_client", lambda _s: gsp)
    monkeypatch.setattr(cron_module, "ClientQuery", lambda **_kw: clients_q)
    monkeypatch.setattr(cron_module.auto_ingest, "ingest_gstr2b_bytes", ingest)
    monkeypatch.setattr(cron_module, "get_sessionmaker", _fake_sessionmaker)

    await cron_module._gstr2b_pull_for_firm(firm)

    gsp.fetch_gstr2b.assert_awaited_once()
    assert gsp.fetch_gstr2b.await_args.kwargs["gstin"] == "27AABCC1234D1Z5"
    assert gsp.fetch_gstr2b.await_args.kwargs["auth_token"] == "otp-token"
    ingest.assert_awaited_once()


async def test_gstr2b_pull_dormant_when_unconfigured(monkeypatch: Any) -> None:
    gsp = MagicMock()
    gsp.is_configured = False
    gsp.fetch_gstr2b = AsyncMock()
    monkeypatch.setattr(cron_module, "get_gsp_client", lambda _s: gsp)

    await cron_module._gstr2b_pull_for_firm(SimpleNamespace(id=uuid.uuid4()))

    gsp.fetch_gstr2b.assert_not_awaited()  # never pulls when no GSP configured
