"""Tests — the Phase 8c client-facing completeness chase cron.

Exercises ``_completeness_chase_for_firm`` directly with a fake session
factory + monkeypatched query classes + a stubbed ``assess_firm_completeness``
and a mocked telegram service, matching the pattern in
``test_cron_recon_report.py``.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from app.api.routes import cron as cron_module
from app.services import telegram as telegram_service
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


def _firm() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), firm_name="Acme CA", admin_chat_id=42)


def _client(name: str, chat_id: int | None) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), trade_name=name, telegram_chat_id=chat_id)


def _report(client: SimpleNamespace, *, present: set[str]) -> ClientCompleteness:
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


def _wire(
    monkeypatch: Any,
    *,
    clients: list[SimpleNamespace],
    reports: list[ClientCompleteness],
    already_notified: set[uuid.UUID] | None = None,
) -> tuple[list[dict[str, Any]], list[uuid.UUID]]:
    """Patch the cron module's deps; return (telegram send sink, recorded ids)."""
    notified = already_notified or set()
    recorded: list[uuid.UUID] = []

    class _FakeClientQuery:
        def __init__(self, **_kw: Any) -> None: ...

        async def list_active(self) -> list[SimpleNamespace]:
            return clients

    class _FakeNotificationQuery:
        def __init__(self, **_kw: Any) -> None: ...

        async def was_recently_notified(
            self, *, client_id: uuid.UUID, notification_type: str
        ) -> bool:
            return client_id in notified

        async def record_notification(
            self, *, client_id: uuid.UUID, notification_type: str
        ) -> None:
            recorded.append(client_id)

    async def _fake_assess(**_kw: Any) -> list[ClientCompleteness]:
        return reports

    sent: list[dict[str, Any]] = []

    async def _send(*, chat_id: int, html_text: str, **_kw: Any) -> dict[str, Any]:
        sent.append({"chat_id": chat_id, "html": html_text})
        return {"message_id": 1}

    monkeypatch.setattr(cron_module, "get_sessionmaker", _fake_sessionmaker)
    monkeypatch.setattr(cron_module, "ClientQuery", _FakeClientQuery)
    monkeypatch.setattr(cron_module, "NotificationQuery", _FakeNotificationQuery)
    monkeypatch.setattr(cron_module, "assess_firm_completeness", _fake_assess)
    monkeypatch.setattr(telegram_service, "send_message", _send)
    return sent, recorded


async def test_incomplete_bound_client_is_chased(monkeypatch: Any) -> None:
    c = _client("S.S Traders", chat_id=555)
    sent, recorded = _wire(
        monkeypatch,
        clients=[c],
        reports=[_report(c, present={LEG_BOOKS})],  # missing gstr2b + bank
    )
    await cron_module._completeness_chase_for_firm(_firm())

    assert len(sent) == 1
    assert sent[0]["chat_id"] == 555  # the client's bound chat, not the CA
    body = sent[0]["html"]
    assert "GSTR-2B (ITC)" in body
    assert "Bank statement" in body
    assert "Purchase invoices" not in body  # books already in → not chased
    assert recorded == [c.id]  # cooldown recorded


async def test_complete_client_is_not_chased(monkeypatch: Any) -> None:
    c = _client("All Set Co", chat_id=777)
    sent, recorded = _wire(
        monkeypatch,
        clients=[c],
        reports=[_report(c, present={LEG_BOOKS, LEG_GSTR2B, LEG_BANK})],
    )
    await cron_module._completeness_chase_for_firm(_firm())
    assert sent == []
    assert recorded == []


async def test_unbound_client_is_skipped(monkeypatch: Any) -> None:
    """A client who never tapped their deep link can't be reached directly."""
    c = _client("No Telegram Co", chat_id=None)
    sent, recorded = _wire(
        monkeypatch, clients=[c], reports=[_report(c, present=set())]
    )
    await cron_module._completeness_chase_for_firm(_firm())
    assert sent == []
    assert recorded == []


async def test_cooldown_suppresses_repeat(monkeypatch: Any) -> None:
    c = _client("Recently Nudged", chat_id=999)
    sent, recorded = _wire(
        monkeypatch,
        clients=[c],
        reports=[_report(c, present={LEG_BOOKS})],
        already_notified={c.id},
    )
    await cron_module._completeness_chase_for_firm(_firm())
    assert sent == []
    assert recorded == []


async def test_batch_cap_limits_sends(monkeypatch: Any) -> None:
    clients = [_client(f"Client {i}", chat_id=1000 + i) for i in range(5)]
    reports = [_report(c, present=set()) for c in clients]
    sent, recorded = _wire(monkeypatch, clients=clients, reports=reports)
    await cron_module._completeness_chase_for_firm(_firm(), batch_cap=2)
    assert len(sent) == 2
    assert len(recorded) == 2
