"""End-to-end tests for Phase 6 query classes via in-memory SQLite.

The schema is reduced to what the queries actually touch — primary keys,
columns referenced in SELECT/WHERE, and the minimum constraints.

Covers:
    * ``PendingAssignmentQuery``  — create / get / list_open / resolve
    * ``TaskQuery``               — create / list_open / list_overdue / mark_complete
    * ``FilingQuery``             — create_approval / is_period_locked / list_locked_periods
    * ``ConversationQuery``       — append_turn / list_recent_live / get_last_client_id
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from app.db.queries.conversations import ConversationQuery
from app.db.queries.filings import FilingAlreadyApproved, FilingQuery
from app.db.queries.pending_assignments import PendingAssignmentQuery
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
    """CREATE TABLE pending_assignments (
        id TEXT PRIMARY KEY, ca_firm_id TEXT NOT NULL,
        chat_id INTEGER NOT NULL, message_id INTEGER,
        file_ids TEXT NOT NULL, candidate_client_ids TEXT NOT NULL,
        prompt_message_id INTEGER, resolved_client_id TEXT,
        resolved_at TEXT, expires_at TEXT NOT NULL, created_at TEXT
    )""",
    """CREATE TABLE tasks (
        id TEXT PRIMARY KEY, ca_firm_id TEXT NOT NULL, client_id TEXT,
        title TEXT NOT NULL, description TEXT, status TEXT DEFAULT 'pending',
        priority INTEGER DEFAULT 0, due_at TEXT, last_notified_at TEXT,
        created_at TEXT, updated_at TEXT
    )""",
    """CREATE TABLE filing_approvals (
        id TEXT PRIMARY KEY, ca_firm_id TEXT NOT NULL,
        filing_month INTEGER NOT NULL, filing_year INTEGER NOT NULL,
        approved_by_chat_id INTEGER NOT NULL, filing_snapshot TEXT NOT NULL,
        approval_hash TEXT NOT NULL, created_at TEXT,
        UNIQUE(ca_firm_id, filing_month, filing_year)
    )""",
    """CREATE TABLE conversations (
        id TEXT PRIMARY KEY, ca_firm_id TEXT NOT NULL,
        chat_id INTEGER NOT NULL, client_id TEXT, role TEXT NOT NULL,
        content TEXT NOT NULL, message_id INTEGER, metadata TEXT NOT NULL,
        created_at TEXT
    )""",
]


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        for ddl in _DDL:
            await conn.execute(sa_text(ddl))
        # seed firm
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
# PendingAssignmentQuery
# ---------------------------------------------------------------------------


class TestPendingAssignmentQuery:
    @pytest.mark.asyncio
    async def test_create_and_list_open(self, session: AsyncSession) -> None:
        q = PendingAssignmentQuery(session=session, ca_firm_id=FIRM_ID)
        row = await q.create(chat_id=42, file_ids=["f1", "f2"], message_id=1)
        await session.commit()
        assert row.chat_id == 42
        rows = await q.list_open_for_chat(chat_id=42)
        assert len(rows) == 1
        assert rows[0].id == row.id

    @pytest.mark.asyncio
    async def test_resolve_marks_done(self, session: AsyncSession) -> None:
        q = PendingAssignmentQuery(session=session, ca_firm_id=FIRM_ID)
        row = await q.create(chat_id=42, file_ids=["f1"])
        await session.commit()
        client_id = uuid.uuid4()
        resolved = await q.resolve(pending_id=row.id, client_id=client_id)
        await session.commit()
        assert resolved is not None
        assert resolved.resolved_client_id == client_id
        # No longer open.
        assert await q.list_open_for_chat(chat_id=42) == []

    @pytest.mark.asyncio
    async def test_get_open_skips_expired(self, session: AsyncSession) -> None:
        q = PendingAssignmentQuery(session=session, ca_firm_id=FIRM_ID)
        # ttl_seconds=-1 → already expired the instant we create.
        row = await q.create(chat_id=42, file_ids=["f1"], ttl_seconds=-1)
        await session.commit()
        assert await q.get_open_by_id(row.id) is None

    @pytest.mark.asyncio
    async def test_attach_prompt_message(self, session: AsyncSession) -> None:
        q = PendingAssignmentQuery(session=session, ca_firm_id=FIRM_ID)
        row = await q.create(chat_id=42, file_ids=["f1"])
        await session.commit()
        updated = await q.attach_prompt_message(row.id, prompt_message_id=99)
        assert updated is not None
        assert updated.prompt_message_id == 99


# ---------------------------------------------------------------------------
# TaskQuery
# ---------------------------------------------------------------------------


class TestTaskQuery:
    @pytest.mark.asyncio
    async def test_create_and_list_open(self, session: AsyncSession) -> None:
        q = TaskQuery(session=session, ca_firm_id=FIRM_ID)
        await q.create(title="A")
        await q.create(title="B", priority=5)
        await session.commit()
        open_rows = list(await q.list_open())
        assert len(open_rows) == 2
        assert open_rows[0].title == "B"  # priority desc

    @pytest.mark.asyncio
    async def test_mark_complete(self, session: AsyncSession) -> None:
        q = TaskQuery(session=session, ca_firm_id=FIRM_ID)
        t = await q.create(title="One")
        await session.commit()
        done = await q.mark_complete(t.id)
        assert done is not None
        assert done.status == "completed"
        assert list(await q.list_open()) == []

    @pytest.mark.asyncio
    async def test_list_overdue(self, session: AsyncSession) -> None:
        q = TaskQuery(session=session, ca_firm_id=FIRM_ID)
        past = datetime.now(UTC) - timedelta(days=1)
        await q.create(title="overdue", due_at=past)
        await q.create(title="future", due_at=datetime.now(UTC) + timedelta(days=1))
        await session.commit()
        overdue = list(await q.list_overdue())
        assert len(overdue) == 1
        assert overdue[0].title == "overdue"

    @pytest.mark.asyncio
    async def test_update_status_validates(self, session: AsyncSession) -> None:
        q = TaskQuery(session=session, ca_firm_id=FIRM_ID)
        t = await q.create(title="X")
        await session.commit()
        with pytest.raises(ValueError, match="unknown task status"):
            await q.update_status(t.id, status="bogus")


# ---------------------------------------------------------------------------
# FilingQuery
# ---------------------------------------------------------------------------


class TestFilingQuery:
    @pytest.mark.asyncio
    async def test_create_and_lookup(self, session: AsyncSession) -> None:
        q = FilingQuery(session=session, ca_firm_id=FIRM_ID)
        snap = {"hello": "world"}
        approval = await q.create_approval(
            month=3,
            year=2026,
            approved_by_chat_id=1,
            filing_snapshot=snap,
            approval_hash="deadbeef" * 8,
        )
        await session.commit()
        assert approval.filing_month == 3
        assert await q.is_period_locked(month=3, year=2026) is True
        assert await q.is_period_locked(month=4, year=2026) is False
        locked = await q.list_locked_periods()
        assert (3, 2026) in locked

    @pytest.mark.asyncio
    async def test_duplicate_raises(self, session: AsyncSession) -> None:
        q = FilingQuery(session=session, ca_firm_id=FIRM_ID)
        await q.create_approval(
            month=3, year=2026, approved_by_chat_id=1,
            filing_snapshot={}, approval_hash="x" * 64,
        )
        await session.commit()
        with pytest.raises(FilingAlreadyApproved):
            await q.create_approval(
                month=3, year=2026, approved_by_chat_id=1,
                filing_snapshot={}, approval_hash="y" * 64,
            )


# ---------------------------------------------------------------------------
# ConversationQuery
# ---------------------------------------------------------------------------


class TestConversationQuery:
    @pytest.mark.asyncio
    async def test_append_and_recent(self, session: AsyncSession) -> None:
        q = ConversationQuery(session=session, ca_firm_id=FIRM_ID)
        client_id = uuid.uuid4()
        await q.append_turn(chat_id=42, role="user", content="hi")
        await q.append_turn(
            chat_id=42, role="assistant", content="hello", client_id=client_id
        )
        await session.commit()
        recent = await q.list_recent_live(chat_id=42, limit=10)
        assert len(recent) == 2
        roles = {t.role for t in recent}
        assert roles == {"user", "assistant"}
        # The assistant turn carries the client_id.
        assistant = next(t for t in recent if t.role == "assistant")
        assert assistant.client_id == client_id

    @pytest.mark.asyncio
    async def test_get_last_client_id(self, session: AsyncSession) -> None:
        q = ConversationQuery(session=session, ca_firm_id=FIRM_ID)
        client_id = uuid.uuid4()
        await q.append_turn(chat_id=42, role="user", content="x", client_id=client_id)
        await session.commit()
        assert await q.get_last_client_id(chat_id=42) == client_id

    @pytest.mark.asyncio
    async def test_get_last_client_id_none(self, session: AsyncSession) -> None:
        q = ConversationQuery(session=session, ca_firm_id=FIRM_ID)
        await q.append_turn(chat_id=42, role="user", content="x")
        await session.commit()
        assert await q.get_last_client_id(chat_id=42) is None


# ---------------------------------------------------------------------------
# Cross-tenant safety
# ---------------------------------------------------------------------------


class TestPhase6TenantIsolation:
    @pytest.mark.asyncio
    async def test_pending_assignment_scoped_to_firm(
        self, session: AsyncSession
    ) -> None:
        other_firm = uuid.uuid4()
        # Seed the other firm so the FK is satisfiable.
        await session.execute(
            sa_text(
                "INSERT INTO ca_firms (id, firm_name, admin_chat_id, "
                "telegram_bot_token, subscription_tier, max_clients, "
                "created_at, updated_at) VALUES (:id, 'B', 2, 't', 's', 50, :n, :n)"
            ),
            {"id": str(other_firm), "n": datetime.now(UTC).isoformat()},
        )
        await session.commit()
        q_a = PendingAssignmentQuery(session=session, ca_firm_id=FIRM_ID)
        q_b = PendingAssignmentQuery(session=session, ca_firm_id=other_firm)
        row = await q_a.create(chat_id=99, file_ids=["x"])
        await session.commit()
        # Firm B cannot see firm A's row.
        assert await q_b.get_open_by_id(row.id) is None
        assert await q_b.list_open_for_chat(chat_id=99) == []

    @pytest.mark.asyncio
    async def test_task_scoped_to_firm(self, session: AsyncSession) -> None:
        other = uuid.uuid4()
        await session.execute(
            sa_text(
                "INSERT INTO ca_firms (id, firm_name, admin_chat_id, "
                "telegram_bot_token, subscription_tier, max_clients, "
                "created_at, updated_at) VALUES (:id, 'B', 3, 't', 's', 50, :n, :n)"
            ),
            {"id": str(other), "n": datetime.now(UTC).isoformat()},
        )
        await session.commit()
        q_a = TaskQuery(session=session, ca_firm_id=FIRM_ID)
        q_b = TaskQuery(session=session, ca_firm_id=other)
        await q_a.create(title="firm A task")
        await session.commit()
        assert list(await q_b.list_open()) == []


# Smoke-test that ``metadata`` JSONB lookups work via SQLAlchemy
# (compaction tombstone path depends on this).
@pytest.mark.asyncio
async def test_metadata_json_passthrough(session: AsyncSession) -> None:
    q = ConversationQuery(session=session, ca_firm_id=FIRM_ID)
    await q.append_turn(
        chat_id=1, role="system", content="seed", metadata={"kind": "summary"}
    )
    await session.commit()
    rows = await q.list_recent_live(chat_id=1, limit=5)
    # SQLite stores JSONB as TEXT — pydantic-side it's still a dict.
    assert rows[0].content == "seed"
    assert json.loads(rows[0].metadata_) == {"kind": "summary"} if isinstance(
        rows[0].metadata_, str
    ) else rows[0].metadata_ == {"kind": "summary"}
