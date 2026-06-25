"""Tests — the monthly reconciliation digest cron (Track-A automation P1).

Exercises ``_recon_report_for_firm`` directly with a fake session factory +
monkeypatched query classes and a mocked telegram service, matching the
pattern in ``test_cron_routes.py``.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from app.api.routes import cron as cron_module
from app.services import telegram as telegram_service


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


def _firm() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), firm_name="Acme CA", admin_chat_id=42)


def _client(name: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), trade_name=name)


def _run(month: int, year: int, recoverable: str, at_risk: str) -> SimpleNamespace:
    return SimpleNamespace(
        filing_period_month=month,
        filing_period_year=year,
        recoverable_itc=Decimal(recoverable),
        at_risk_itc=Decimal(at_risk),
    )


def _wire(
    monkeypatch: Any,
    *,
    clients: list[SimpleNamespace],
    sums: dict[Any, dict[str, Decimal]],
    runs: dict[Any, list[SimpleNamespace]],
) -> list[dict[str, Any]]:
    """Patch the cron module's query classes + telegram; return a send sink."""

    class _FakeClientQuery:
        def __init__(self, **_kw: Any) -> None: ...

        async def list_active(self) -> list[SimpleNamespace]:
            return clients

    class _FakeOutcomeQuery:
        def __init__(self, **_kw: Any) -> None: ...

        async def sum_by_kind(
            self, *, kind: str, client_id: Any = None
        ) -> Decimal:
            return sums.get(client_id, {}).get(kind, Decimal("0"))

    class _FakeReconRunQuery:
        def __init__(self, **_kw: Any) -> None: ...

        async def list_recent(
            self, *, limit: int = 25, client_id: Any = None
        ) -> list[SimpleNamespace]:
            return runs.get(client_id, [])[:limit]

    sent: list[dict[str, Any]] = []

    async def _send(*, chat_id: int, html_text: str, **_kw: Any) -> dict[str, Any]:
        sent.append({"chat_id": chat_id, "html": html_text})
        return {"message_id": 1}

    monkeypatch.setattr(cron_module, "get_sessionmaker", _fake_sessionmaker)
    monkeypatch.setattr(cron_module, "ClientQuery", _FakeClientQuery)
    monkeypatch.setattr(cron_module, "OutcomeUnitQuery", _FakeOutcomeQuery)
    monkeypatch.setattr(cron_module, "ReconRunQuery", _FakeReconRunQuery)
    monkeypatch.setattr(telegram_service, "send_message", _send)
    return sent


async def test_digest_sent_with_activity(monkeypatch: Any) -> None:
    active = _client("S.S Traders")
    quiet = _client("Dormant Co")
    sent = _wire(
        monkeypatch,
        clients=[active, quiet],
        sums={active.id: {"itc_recovered_inr": Decimal("94.28")}},
        runs={active.id: [_run(3, 2026, "94.28", "200.00")]},
    )
    await cron_module._recon_report_for_firm(_firm())

    assert len(sent) == 1
    body = sent[0]["html"]
    assert sent[0]["chat_id"] == 42
    assert "S.S Traders" in body
    assert "94.28" in body
    assert "03/2026" in body
    assert "Dormant Co" not in body  # no activity → excluded


async def test_no_activity_stays_quiet(monkeypatch: Any) -> None:
    quiet = _client("Dormant Co")
    sent = _wire(monkeypatch, clients=[quiet], sums={}, runs={})
    await cron_module._recon_report_for_firm(_firm())
    assert sent == []  # nothing reconciled → no digest


async def test_reversal_risk_shown(monkeypatch: Any) -> None:
    c = _client("Risky Traders")
    sent = _wire(
        monkeypatch,
        clients=[c],
        sums={c.id: {"itc_reversal_risk_inr": Decimal("51000.00")}},
        runs={},
    )
    await cron_module._recon_report_for_firm(_firm())
    assert len(sent) == 1
    assert "Reversal risk" in sent[0]["html"]
    assert "51000.00" in sent[0]["html"]
