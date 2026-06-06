"""Tests for the per-firm cron coroutines (heartbeat, briefing, chase).

We exercise the actual functions (not the FastAPI routes) with a fake
session factory + monkeypatched query classes and a mocked
:mod:`telegram` service. This covers the bulk of ``app/api/routes/cron.py``
without needing a live backend.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from app.api.routes import cron as cron_module
from app.services import scraper
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


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeatForFirm:
    @pytest.mark.asyncio
    async def test_no_overdue_no_send(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sends: list[Any] = []

        class _FakeTaskQuery:
            def __init__(self, **_k: Any) -> None: ...
            async def list_due_for_heartbeat(self) -> list[Any]:
                return []

        monkeypatch.setattr(cron_module, "get_sessionmaker", _fake_sessionmaker)
        monkeypatch.setattr(cron_module, "TaskQuery", _FakeTaskQuery)

        async def _send(**kw: Any) -> dict[str, Any]:
            sends.append(kw)
            return {}

        monkeypatch.setattr(telegram_service, "send_message", _send)
        await cron_module._heartbeat_for_firm(_firm())
        assert sends == []

    @pytest.mark.asyncio
    async def test_overdue_sends_and_marks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sends: list[Any] = []
        marked: list[Any] = []

        task = SimpleNamespace(
            id=uuid.uuid4(),
            title="GSTR-3B due",
            due_at=datetime.now(UTC) - timedelta(hours=2),
        )

        class _FakeTaskQuery:
            def __init__(self, **_k: Any) -> None: ...
            async def list_due_for_heartbeat(self) -> list[Any]:
                return [task]
            async def mark_notified(self, task_id: Any) -> Any:
                marked.append(task_id)
                return task

        monkeypatch.setattr(cron_module, "get_sessionmaker", _fake_sessionmaker)
        monkeypatch.setattr(cron_module, "TaskQuery", _FakeTaskQuery)

        async def _send(**kw: Any) -> dict[str, Any]:
            sends.append(kw)
            return {}

        monkeypatch.setattr(telegram_service, "send_message", _send)
        await cron_module._heartbeat_for_firm(_firm())
        assert len(sends) == 1
        assert "GSTR-3B due" in sends[0]["html_text"]
        assert marked == [task.id]


# ---------------------------------------------------------------------------
# Briefing
# ---------------------------------------------------------------------------


class TestBriefingForFirm:
    @pytest.mark.asyncio
    async def test_renders_and_sends(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sends: list[Any] = []

        class _FakeTaskQuery:
            def __init__(self, **_k: Any) -> None: ...
            async def list_open(self) -> list[Any]:
                return []
            async def list_overdue(self) -> list[Any]:
                return []

        class _FakeDocQuery:
            def __init__(self, **_k: Any) -> None: ...
            async def list_by_filing_period(self, *, year: int, month: int) -> list[Any]:
                return []

        monkeypatch.setattr(cron_module, "get_sessionmaker", _fake_sessionmaker)
        monkeypatch.setattr(cron_module, "TaskQuery", _FakeTaskQuery)
        monkeypatch.setattr(cron_module, "DocumentQuery", _FakeDocQuery)
        scraper.reset_cache()
        monkeypatch.setattr(
            cron_module.scraper, "fetch_compliance_news", AsyncMock(return_value=())
        )

        async def _send(**kw: Any) -> dict[str, Any]:
            sends.append(kw)
            return {}

        monkeypatch.setattr(telegram_service, "send_message", _send)
        await cron_module._briefing_for_firm(_firm())
        assert len(sends) == 1
        assert "Morning brief" in sends[0]["html_text"]

    @pytest.mark.asyncio
    async def test_scraper_failure_does_not_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sends: list[Any] = []

        class _FakeTaskQuery:
            def __init__(self, **_k: Any) -> None: ...
            async def list_open(self) -> list[Any]:
                return []
            async def list_overdue(self) -> list[Any]:
                return []

        class _FakeDocQuery:
            def __init__(self, **_k: Any) -> None: ...
            async def list_by_filing_period(self, *, year: int, month: int) -> list[Any]:
                return []

        monkeypatch.setattr(cron_module, "get_sessionmaker", _fake_sessionmaker)
        monkeypatch.setattr(cron_module, "TaskQuery", _FakeTaskQuery)
        monkeypatch.setattr(cron_module, "DocumentQuery", _FakeDocQuery)
        scraper.reset_cache()

        async def _boom() -> tuple[Any, ...]:
            raise scraper.ScraperError("upstream down")

        monkeypatch.setattr(cron_module.scraper, "fetch_compliance_news", _boom)

        async def _send(**kw: Any) -> dict[str, Any]:
            sends.append(kw)
            return {}

        monkeypatch.setattr(telegram_service, "send_message", _send)
        await cron_module._briefing_for_firm(_firm())
        assert len(sends) == 1
        assert "No fresh items today" in sends[0]["html_text"]


# ---------------------------------------------------------------------------
# Client chase
# ---------------------------------------------------------------------------


class TestChaseForFirm:
    @pytest.mark.asyncio
    async def test_batch_cap_enforced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sends: list[Any] = []
        candidates = [
            SimpleNamespace(id=uuid.uuid4(), trade_name=f"Client {i}")
            for i in range(25)
        ]
        recorded: list[Any] = []

        class _FakeClientQuery:
            def __init__(self, **_k: Any) -> None: ...
            async def list_missing_filing_docs(self, *, month: int, year: int) -> list[Any]:
                return candidates

        class _FakeNotificationQuery:
            def __init__(self, **_k: Any) -> None: ...
            async def was_recently_notified(self, *, client_id: Any) -> bool:
                return False
            async def record_notification(self, *, client_id: Any) -> Any:
                recorded.append(client_id)
                return SimpleNamespace(id=uuid.uuid4())

        monkeypatch.setattr(cron_module, "get_sessionmaker", _fake_sessionmaker)
        monkeypatch.setattr(cron_module, "ClientQuery", _FakeClientQuery)
        monkeypatch.setattr(cron_module, "NotificationQuery", _FakeNotificationQuery)

        async def _send(**kw: Any) -> dict[str, Any]:
            sends.append(kw)
            return {}

        monkeypatch.setattr(telegram_service, "send_message", _send)
        await cron_module._chase_for_firm(_firm(), batch_cap=5)
        assert len(sends) == 5
        assert len(recorded) == 5

    @pytest.mark.asyncio
    async def test_cooldown_skips_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sends: list[Any] = []
        candidates = [
            SimpleNamespace(id=uuid.uuid4(), trade_name="A"),
            SimpleNamespace(id=uuid.uuid4(), trade_name="B"),
        ]
        cooled_down = {candidates[0].id}

        class _FakeClientQuery:
            def __init__(self, **_k: Any) -> None: ...
            async def list_missing_filing_docs(self, *, month: int, year: int) -> list[Any]:
                return candidates

        class _FakeNotificationQuery:
            def __init__(self, **_k: Any) -> None: ...
            async def was_recently_notified(self, *, client_id: Any) -> bool:
                return client_id in cooled_down
            async def record_notification(self, *, client_id: Any) -> Any:
                return SimpleNamespace(id=uuid.uuid4())

        monkeypatch.setattr(cron_module, "get_sessionmaker", _fake_sessionmaker)
        monkeypatch.setattr(cron_module, "ClientQuery", _FakeClientQuery)
        monkeypatch.setattr(cron_module, "NotificationQuery", _FakeNotificationQuery)

        async def _send(**kw: Any) -> dict[str, Any]:
            sends.append(kw)
            return {}

        monkeypatch.setattr(telegram_service, "send_message", _send)
        await cron_module._chase_for_firm(_firm(), batch_cap=10)
        assert len(sends) == 1
        assert "B" in sends[0]["html_text"]


# ---------------------------------------------------------------------------
# Fan-out
# ---------------------------------------------------------------------------


class TestFanout:
    @pytest.mark.asyncio
    async def test_one_failure_does_not_break_others(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        firms = [_firm(), _firm(), _firm()]
        seen: list[Any] = []

        async def _list_all_firms(_session: Any) -> list[Any]:
            return firms

        monkeypatch.setattr(cron_module, "list_all_firms", _list_all_firms)
        monkeypatch.setattr(cron_module, "get_sessionmaker", _fake_sessionmaker)

        async def _per_firm(firm: Any) -> None:
            seen.append(firm.id)
            if len(seen) == 2:
                raise RuntimeError("middle one explodes")

        await cron_module._fanout(_per_firm, label="test")
        assert len(seen) == 3
        assert seen == [f.id for f in firms]


# ---------------------------------------------------------------------------
# Period helper
# ---------------------------------------------------------------------------


class TestNextPeriod:
    def test_simple_increment(self) -> None:
        from app.utils.date_utils import FilingPeriod

        nxt = cron_module._next_period(FilingPeriod(year=2026, month=3))
        assert nxt == FilingPeriod(year=2026, month=4)

    def test_year_wrap(self) -> None:
        from app.utils.date_utils import FilingPeriod

        nxt = cron_module._next_period(FilingPeriod(year=2026, month=12))
        assert nxt == FilingPeriod(year=2027, month=1)
