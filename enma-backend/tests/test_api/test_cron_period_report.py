"""Tests — the Phase 8d filing-ready report auto-assembly cron.

Exercises ``_period_report_for_firm`` directly: gate on completeness, gate on
"already delivered this period", deliver to the CA without re-billing.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from app.api.routes import cron as cron_module
from app.services.completeness import (
    LEG_BANK,
    LEG_BOOKS,
    LEG_GSTR2B,
    ClientCompleteness,
    LegStatus,
)


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


def _firm(admin_chat_id: int | None = 42) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), firm_name="Acme CA", admin_chat_id=admin_chat_id)


def _client(name: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), trade_name=name, telegram_chat_id=None)


def _complete(client: SimpleNamespace, *, present: set[str]) -> ClientCompleteness:
    return ClientCompleteness(
        client_id=client.id,
        trade_name=client.trade_name,
        month=3,
        year=2026,
        legs=tuple(
            LegStatus(leg=leg, present=leg in present, count=1 if leg in present else 0)
            for leg in (LEG_BOOKS, LEG_GSTR2B, LEG_BANK)
        ),
    )


_ALL = {LEG_BOOKS, LEG_GSTR2B, LEG_BANK}


def _wire(
    monkeypatch: Any,
    *,
    clients: list[SimpleNamespace],
    completeness: dict[uuid.UUID, set[str]],
    already_delivered: set[uuid.UUID] | None = None,
) -> list[dict[str, Any]]:
    """Patch the cron deps; return a sink of reconcile_and_deliver calls."""
    delivered = already_delivered or set()

    class _FakeClientQuery:
        def __init__(self, **_kw: Any) -> None: ...

        async def list_active(self) -> list[SimpleNamespace]:
            return clients

    class _FakeReconRunQuery:
        def __init__(self, **_kw: Any) -> None: ...

        async def exists_for_period(
            self, *, client_id: uuid.UUID, filing_period_month: int,
            filing_period_year: int, kind: str | None = None,
        ) -> bool:
            return client_id in delivered

    async def _fake_assess(
        *, client_id: uuid.UUID, trade_name: str, **_kw: Any
    ) -> ClientCompleteness:
        stub = SimpleNamespace(id=client_id, trade_name=trade_name)
        return _complete(stub, present=completeness.get(client_id, set()))

    async def _fake_entries(**_kw: Any) -> list[Any]:
        return []

    calls: list[dict[str, Any]] = []

    async def _fake_deliver(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(run_id=uuid.uuid4())

    monkeypatch.setattr(cron_module, "get_sessionmaker", _fake_sessionmaker)
    monkeypatch.setattr(cron_module, "ClientQuery", _FakeClientQuery)
    monkeypatch.setattr(cron_module, "ReconRunQuery", _FakeReconRunQuery)
    monkeypatch.setattr(cron_module, "assess_completeness", _fake_assess)
    monkeypatch.setattr(cron_module, "entries_from_brain", _fake_entries)
    monkeypatch.setattr(cron_module, "reconcile_and_deliver", _fake_deliver)
    return calls


async def test_complete_client_gets_report_without_rebilling(monkeypatch: Any) -> None:
    c = _client("S.S Traders")
    calls = _wire(monkeypatch, clients=[c], completeness={c.id: _ALL})
    firm = _firm(admin_chat_id=42)
    await cron_module._period_report_for_firm(firm)

    assert len(calls) == 1
    call = calls[0]
    assert call["chat_id"] == 42  # delivered to the CA
    assert call["client"] is c
    assert call["record_outcomes"] is False  # interim recon already billed
    assert call["run_kind"] == cron_module.PERIOD_REPORT_KIND


async def test_incomplete_client_is_skipped(monkeypatch: Any) -> None:
    c = _client("Not Ready Co")
    calls = _wire(monkeypatch, clients=[c], completeness={c.id: {LEG_BOOKS, LEG_BANK}})
    await cron_module._period_report_for_firm(_firm())
    assert calls == []


async def test_already_delivered_period_is_skipped(monkeypatch: Any) -> None:
    c = _client("Done Already Co")
    calls = _wire(
        monkeypatch,
        clients=[c],
        completeness={c.id: _ALL},
        already_delivered={c.id},
    )
    await cron_module._period_report_for_firm(_firm())
    assert calls == []  # idempotent — one report per period


async def test_no_admin_chat_delivers_nothing(monkeypatch: Any) -> None:
    c = _client("Orphan Firm Client")
    calls = _wire(monkeypatch, clients=[c], completeness={c.id: _ALL})
    await cron_module._period_report_for_firm(_firm(admin_chat_id=None))
    assert calls == []


async def test_mixed_firm_reports_only_complete_unsent(monkeypatch: Any) -> None:
    ready = _client("Ready Co")
    not_ready = _client("Pending Co")
    done = _client("Sent Co")
    calls = _wire(
        monkeypatch,
        clients=[ready, not_ready, done],
        completeness={ready.id: _ALL, not_ready.id: {LEG_GSTR2B}, done.id: _ALL},
        already_delivered={done.id},
    )
    await cron_module._period_report_for_firm(_firm())
    assert len(calls) == 1
    assert calls[0]["client"] is ready
