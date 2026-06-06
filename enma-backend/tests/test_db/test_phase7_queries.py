"""End-to-end tests for Phase 7 query surface via in-memory SQLite.

Covers:
    * ``NotificationQuery``        — cooldown enforcement
    * ``TaskQuery.list_due_for_heartbeat`` / ``mark_notified``
    * ``ClientQuery.list_missing_filing_docs``
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from app.db.queries.clients import ClientQuery
from app.db.queries.notifications import NotificationQuery
from app.db.queries.tasks import TaskQuery
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

FIRM_ID = uuid.uuid4()

sqlite3.register_adapter(uuid.UUID, str)


_DDL = [
    """CREATE TABLE ca_firms (
        id TEXT PRIMARY KEY, firm_name TEXT, admin_chat_id INTEGER,
        telegram_bot_token TEXT, subscription_tier TEXT, max_clients INTEGER,
        created_at TEXT, updated_at TEXT
    )""",
    """CREATE TABLE clients (
        id TEXT PRIMARY KEY, ca_firm_id TEXT NOT NULL,
        trade_name TEXT NOT NULL, legal_name TEXT, gstin TEXT, pan TEXT,
        state_code TEXT, address TEXT, contact_email TEXT, contact_phone TEXT,
        is_active INTEGER DEFAULT 1, gst_tds_deductor INTEGER DEFAULT 0,
        created_at TEXT, updated_at TEXT
    )""",
    """CREATE TABLE documents (
        id TEXT PRIMARY KEY, ca_firm_id TEXT NOT NULL, client_id TEXT NOT NULL,
        document_type TEXT NOT NULL, source_file_ids TEXT NOT NULL,
        extraction_data TEXT NOT NULL, tax_verdict TEXT, verification_result TEXT,
        filing_period_month INTEGER, filing_period_year INTEGER,
        processing_status TEXT DEFAULT 'pending', processing_time_ms INTEGER,
        created_at TEXT, updated_at TEXT
    )""",
    """CREATE TABLE tasks (
        id TEXT PRIMARY KEY, ca_firm_id TEXT NOT NULL, client_id TEXT,
        title TEXT NOT NULL, description TEXT, status TEXT DEFAULT 'pending',
        priority INTEGER DEFAULT 0, due_at TEXT, last_notified_at TEXT,
        created_at TEXT, updated_at TEXT
    )""",
    """CREATE TABLE client_notifications (
        id TEXT PRIMARY KEY, ca_firm_id TEXT NOT NULL, client_id TEXT NOT NULL,
        notification_type TEXT NOT NULL, sent_at TEXT NOT NULL,
        cooldown_until TEXT
    )""",
]


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        for ddl in _DDL:
            await conn.execute(sa_text(ddl))
        await conn.execute(
            sa_text(
                "INSERT INTO ca_firms (id, firm_name, admin_chat_id, "
                "telegram_bot_token, subscription_tier, max_clients, "
                "created_at, updated_at) VALUES (:id, 'F', 1, 't', 's', 50, :n, :n)"
            ),
            {"id": str(FIRM_ID), "n": datetime.now(UTC).isoformat()},
        )
    factory = async_sessionmaker(eng, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await eng.dispose()


# ---------------------------------------------------------------------------
# NotificationQuery
# ---------------------------------------------------------------------------


class TestNotificationQuery:
    @pytest.mark.asyncio
    async def test_cooldown_blocks_then_expires(self, session: AsyncSession) -> None:
        q = NotificationQuery(session=session, ca_firm_id=FIRM_ID)
        client_id = uuid.uuid4()
        # Fresh cooldown row.
        await q.record_notification(client_id=client_id, cooldown_days=1)
        await session.commit()
        assert await q.was_recently_notified(client_id=client_id) is True
        # Pretend a future "now" past the cooldown.
        future = datetime.now(UTC) + timedelta(days=2)
        assert await q.was_recently_notified(client_id=client_id, now=future) is False

    @pytest.mark.asyncio
    async def test_unrelated_client_not_blocked(self, session: AsyncSession) -> None:
        q = NotificationQuery(session=session, ca_firm_id=FIRM_ID)
        await q.record_notification(client_id=uuid.uuid4())
        await session.commit()
        assert await q.was_recently_notified(client_id=uuid.uuid4()) is False


# ---------------------------------------------------------------------------
# TaskQuery.list_due_for_heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeatSelection:
    @pytest.mark.asyncio
    async def test_overdue_with_no_prior_notification(
        self, session: AsyncSession
    ) -> None:
        q = TaskQuery(session=session, ca_firm_id=FIRM_ID)
        past = datetime.now(UTC) - timedelta(hours=2)
        await q.create(title="bills due", due_at=past)
        await session.commit()
        due = list(await q.list_due_for_heartbeat())
        assert len(due) == 1

    @pytest.mark.asyncio
    async def test_recently_notified_skipped(self, session: AsyncSession) -> None:
        q = TaskQuery(session=session, ca_firm_id=FIRM_ID)
        past = datetime.now(UTC) - timedelta(hours=1)
        t = await q.create(title="bills due", due_at=past)
        await q.mark_notified(t.id)
        await session.commit()
        due = list(await q.list_due_for_heartbeat())
        assert due == []

    @pytest.mark.asyncio
    async def test_long_ago_notification_re_fires(
        self, session: AsyncSession
    ) -> None:
        q = TaskQuery(session=session, ca_firm_id=FIRM_ID)
        past = datetime.now(UTC) - timedelta(hours=3)
        t = await q.create(title="bills due", due_at=past)
        # Mark last notified ~2h ago — cooldown is 30 min — so re-fire.
        old_stamp = datetime.now(UTC) - timedelta(hours=2)
        await session.execute(
            sa_text("UPDATE tasks SET last_notified_at = :s WHERE id = :id"),
            {"s": old_stamp.isoformat(), "id": str(t.id)},
        )
        await session.commit()
        due = list(await q.list_due_for_heartbeat())
        assert len(due) == 1


# ---------------------------------------------------------------------------
# ClientQuery.list_missing_filing_docs
# ---------------------------------------------------------------------------


class TestMissingFilingDocs:
    @pytest.mark.asyncio
    async def test_clients_with_no_period_doc_returned(
        self, session: AsyncSession
    ) -> None:
        clients_q = ClientQuery(session=session, ca_firm_id=FIRM_ID)
        a = await clients_q.create(trade_name="Alpha")
        b = await clients_q.create(trade_name="Bravo")
        await session.commit()
        # Insert one document for client A in period 7/2026 only.
        # SQLite stores UUIDs as TEXT — we use the no-hyphen form (`uuid.hex`)
        # to match SQLAlchemy's default rendering of the clients.id values.
        now = datetime.now(UTC).isoformat()
        await session.execute(
            sa_text(
                "INSERT INTO documents (id, ca_firm_id, client_id, document_type, "
                "source_file_ids, extraction_data, filing_period_month, "
                "filing_period_year, processing_status, created_at, updated_at) "
                "VALUES (:id, :firm, :client, 'B2B_INVOICE', '[]', '{}', 7, 2026, "
                "'completed', :n, :n)"
            ),
            {
                "id": uuid.uuid4().hex,
                "firm": FIRM_ID.hex,
                "client": a.id.hex,
                "n": now,
            },
        )
        await session.commit()
        missing = list(
            await clients_q.list_missing_filing_docs(month=7, year=2026)
        )
        assert [c.id for c in missing] == [b.id]
