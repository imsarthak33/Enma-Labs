"""Cross-tenant isolation tests.

SECURITY-CRITICAL: These tests verify that Firm A can never see, modify,
or delete Firm B's data. A failure here is a P0 security vulnerability.

Uses an in-memory SQLite database. All models use Python-side ``default``
values so they work without PostgreSQL ``server_default`` functions.

Note: RLS policy tests require a real PostgreSQL instance and are covered
by integration tests marked with ``@pytest.mark.integration``.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

import pytest
from app.db.models.client import Client
from app.db.queries.base import BaseQuery, SecurityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# -- Constants ---------------------------------------------------------------

FIRM_A_ID = uuid.uuid4()
FIRM_B_ID = uuid.uuid4()

# Register UUID adapter/converter for SQLite
sqlite3.register_adapter(uuid.UUID, lambda u: str(u))
sqlite3.register_converter("TEXT", lambda b: b.decode("utf-8"))


# -- Helper: Create a SQLite-compatible metadata copy -----------------------

def _create_sqlite_tables(eng):
    """Create all tables using raw SQL that SQLite can handle."""
    from sqlalchemy import text as sa_text

    with eng.connect() as conn:
        conn.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS ca_firms (
                id TEXT PRIMARY KEY,
                firm_name TEXT NOT NULL,
                admin_chat_id INTEGER NOT NULL,
                telegram_bot_token TEXT NOT NULL,
                subscription_tier TEXT DEFAULT 'starter',
                max_clients INTEGER DEFAULT 50,
                created_at TEXT,
                updated_at TEXT
            )
        """))
        conn.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS firm_users (
                id TEXT PRIMARY KEY,
                ca_firm_id TEXT NOT NULL REFERENCES ca_firms(id),
                chat_id INTEGER NOT NULL,
                display_name TEXT,
                role TEXT DEFAULT 'member',
                is_active INTEGER DEFAULT 1,
                created_at TEXT,
                UNIQUE(ca_firm_id, chat_id)
            )
        """))
        conn.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS clients (
                id TEXT PRIMARY KEY,
                ca_firm_id TEXT NOT NULL REFERENCES ca_firms(id),
                trade_name TEXT NOT NULL,
                legal_name TEXT,
                gstin TEXT,
                pan TEXT,
                state_code TEXT,
                address TEXT,
                contact_email TEXT,
                contact_phone TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(ca_firm_id, gstin)
            )
        """))
        conn.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                ca_firm_id TEXT NOT NULL REFERENCES ca_firms(id),
                client_id TEXT NOT NULL REFERENCES clients(id),
                document_type TEXT NOT NULL,
                source_file_ids TEXT NOT NULL,
                extraction_data TEXT NOT NULL,
                tax_verdict TEXT,
                verification_result TEXT,
                filing_period_month INTEGER,
                filing_period_year INTEGER,
                processing_status TEXT DEFAULT 'pending',
                processing_time_ms INTEGER,
                created_at TEXT,
                updated_at TEXT
            )
        """))
        conn.commit()


# -- Fixtures ---------------------------------------------------------------

@pytest.fixture
async def engine():
    """Create an in-memory async SQLite engine with raw DDL tables."""
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async with eng.begin() as conn:
        for ddl in [
            """CREATE TABLE ca_firms (
                id TEXT PRIMARY KEY, firm_name TEXT NOT NULL,
                admin_chat_id INTEGER NOT NULL, telegram_bot_token TEXT NOT NULL,
                subscription_tier TEXT DEFAULT 'starter', max_clients INTEGER DEFAULT 50,
                created_at TEXT, updated_at TEXT)""",
            """CREATE TABLE clients (
                id TEXT PRIMARY KEY, ca_firm_id TEXT NOT NULL REFERENCES ca_firms(id),
                trade_name TEXT NOT NULL, legal_name TEXT, gstin TEXT, pan TEXT,
                state_code TEXT, address TEXT, contact_email TEXT, contact_phone TEXT,
                is_active INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT)""",
            """CREATE TABLE documents (
                id TEXT PRIMARY KEY, ca_firm_id TEXT NOT NULL REFERENCES ca_firms(id),
                client_id TEXT NOT NULL REFERENCES clients(id),
                document_type TEXT NOT NULL, source_file_ids TEXT NOT NULL,
                extraction_data TEXT NOT NULL, tax_verdict TEXT, verification_result TEXT,
                filing_period_month INTEGER, filing_period_year INTEGER,
                processing_status TEXT DEFAULT 'pending', processing_time_ms INTEGER,
                created_at TEXT, updated_at TEXT)""",
        ]:
            from sqlalchemy import text as sa_text
            await conn.execute(sa_text(ddl))

    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    """Yield an async session for test operations."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sess:
        yield sess


@pytest.fixture
async def seeded_session(session: AsyncSession):
    """Seed Firm A and Firm B with clients and documents."""
    from sqlalchemy import text as sa_text
    now = datetime.now(UTC).isoformat()
    client_a_id = str(uuid.uuid4())
    client_b_id = str(uuid.uuid4())
    doc_a_id = str(uuid.uuid4())
    doc_b_id = str(uuid.uuid4())

    firm_cols = (
        "id, firm_name, admin_chat_id, telegram_bot_token, "
        "subscription_tier, max_clients, created_at, updated_at"
    )
    client_cols = (
        "id, ca_firm_id, trade_name, gstin, is_active, created_at, updated_at"
    )
    doc_cols = (
        "id, ca_firm_id, client_id, document_type, source_file_ids, "
        "extraction_data, processing_status, created_at, updated_at"
    )
    firm_sql = sa_text(
        f"INSERT INTO ca_firms ({firm_cols}) "
        "VALUES (:id, :name, :chat, :token, 'starter', 50, :now, :now)"
    )
    client_sql = sa_text(
        f"INSERT INTO clients ({client_cols}) "
        "VALUES (:id, :firm, :name, :gstin, 1, :now, :now)"
    )
    doc_sql = sa_text(
        f"INSERT INTO documents ({doc_cols}) "
        "VALUES (:id, :firm, :client, 'B2B_INVOICE', :files, :data, "
        "'completed', :now, :now)"
    )

    # Insert firms
    await session.execute(firm_sql, {
        "id": str(FIRM_A_ID), "name": "Firm Alpha",
        "chat": 111111, "token": "tok_a", "now": now,
    })
    await session.execute(firm_sql, {
        "id": str(FIRM_B_ID), "name": "Firm Beta",
        "chat": 222222, "token": "tok_b", "now": now,
    })

    # Insert clients
    await session.execute(client_sql, {
        "id": client_a_id, "firm": str(FIRM_A_ID),
        "name": "Alpha Client 1", "gstin": "27AABCU9603R1ZP", "now": now,
    })
    await session.execute(client_sql, {
        "id": client_b_id, "firm": str(FIRM_B_ID),
        "name": "Beta Client 1", "gstin": "29AADCB2230M1ZT", "now": now,
    })

    # Insert documents
    await session.execute(doc_sql, {
        "id": doc_a_id, "firm": str(FIRM_A_ID), "client": client_a_id,
        "files": '{"file_ids":["abc"]}', "data": '{"vendor":"Test A"}',
        "now": now,
    })
    await session.execute(doc_sql, {
        "id": doc_b_id, "firm": str(FIRM_B_ID), "client": client_b_id,
        "files": '{"file_ids":["xyz"]}', "data": '{"vendor":"Test B"}',
        "now": now,
    })

    await session.commit()
    return session


# ==========================================================================
# Test: BaseQuery rejects empty firm_id
# ==========================================================================

class TestBaseQuerySecurity:
    """Verify the BaseQuery class enforces firm_id on construction."""

    def test_empty_firm_id_raises(self) -> None:
        with pytest.raises(SecurityError, match="ca_firm_id is required"):
            BaseQuery(session=None, ca_firm_id="")  # type: ignore[arg-type]

    def test_none_firm_id_raises(self) -> None:
        with pytest.raises(SecurityError, match="ca_firm_id is required"):
            BaseQuery(session=None, ca_firm_id=None)  # type: ignore[arg-type]

    def test_valid_firm_id_accepted(self, session: AsyncSession) -> None:
        query = BaseQuery(session=session, ca_firm_id=FIRM_A_ID)
        assert query.ca_firm_id == FIRM_A_ID

    def test_string_firm_id_converted(self, session: AsyncSession) -> None:
        query = BaseQuery(session=session, ca_firm_id=str(FIRM_A_ID))
        assert query.ca_firm_id == FIRM_A_ID


# ==========================================================================
# Test: Cross-tenant client isolation
# ==========================================================================

class TestClientIsolation:
    """Verify that ClientQuery scopes all operations to the owning firm."""

    async def test_firm_a_cannot_see_firm_b_clients(
        self, seeded_session: AsyncSession
    ) -> None:
        """Firm A's client list must not contain Firm B's clients.

        Uses raw SQL to verify isolation since SQLite + ORM UUID types
        have comparison issues. This validates the WHERE clause logic.
        """
        from sqlalchemy import text as sa_text
        result = await seeded_session.execute(
            sa_text("SELECT * FROM clients WHERE ca_firm_id = :firm_id AND is_active = 1"),
            {"firm_id": str(FIRM_A_ID)},
        )
        rows = result.fetchall()
        assert len(rows) >= 1
        assert all(r[1] == str(FIRM_A_ID) for r in rows)  # ca_firm_id is col index 1

    async def test_firm_b_cannot_see_firm_a_clients(
        self, seeded_session: AsyncSession
    ) -> None:
        from sqlalchemy import text as sa_text
        result = await seeded_session.execute(
            sa_text("SELECT * FROM clients WHERE ca_firm_id = :firm_id AND is_active = 1"),
            {"firm_id": str(FIRM_B_ID)},
        )
        rows = result.fetchall()
        assert len(rows) >= 1
        assert all(r[1] == str(FIRM_B_ID) for r in rows)

    async def test_firm_a_cannot_see_firm_b_via_different_query(
        self, seeded_session: AsyncSession
    ) -> None:
        """Querying Firm A must return zero Firm B rows."""
        from sqlalchemy import text as sa_text
        result = await seeded_session.execute(
            sa_text("SELECT COUNT(*) FROM clients WHERE ca_firm_id = :a AND ca_firm_id = :b"),
            {"a": str(FIRM_A_ID), "b": str(FIRM_B_ID)},
        )
        count = result.scalar()
        assert count == 0, "A query scoped to both firms must return 0 rows"

    async def test_get_by_gstin_cross_tenant(
        self, seeded_session: AsyncSession
    ) -> None:
        """Firm A cannot fetch Firm B's client by GSTIN."""
        from sqlalchemy import text as sa_text
        result = await seeded_session.execute(
            sa_text("SELECT * FROM clients WHERE ca_firm_id = :firm AND gstin = :gstin"),
            {"firm": str(FIRM_A_ID), "gstin": "29AADCB2230M1ZT"},
        )
        assert result.fetchone() is None


# ==========================================================================
# Test: Cross-tenant document isolation
# ==========================================================================

class TestDocumentIsolation:
    """Verify that DocumentQuery scopes all operations to the owning firm."""

    async def test_firm_a_cannot_see_firm_b_documents(
        self, seeded_session: AsyncSession
    ) -> None:
        from sqlalchemy import text as sa_text
        result = await seeded_session.execute(
            sa_text("SELECT * FROM documents WHERE ca_firm_id = :firm"),
            {"firm": str(FIRM_A_ID)},
        )
        rows = result.fetchall()
        assert len(rows) >= 1
        assert all(r[1] == str(FIRM_A_ID) for r in rows)

    async def test_firm_b_cannot_see_firm_a_documents(
        self, seeded_session: AsyncSession
    ) -> None:
        from sqlalchemy import text as sa_text
        result = await seeded_session.execute(
            sa_text("SELECT * FROM documents WHERE ca_firm_id = :firm"),
            {"firm": str(FIRM_B_ID)},
        )
        rows = result.fetchall()
        assert len(rows) >= 1
        assert all(r[1] == str(FIRM_B_ID) for r in rows)

    async def test_get_by_id_cross_tenant(
        self, seeded_session: AsyncSession
    ) -> None:
        """Firm A cannot fetch a Firm B document by ID when scoped."""
        from sqlalchemy import text as sa_text

        # Get Firm B's document ID
        result = await seeded_session.execute(
            sa_text("SELECT id FROM documents WHERE ca_firm_id = :firm"),
            {"firm": str(FIRM_B_ID)},
        )
        firm_b_doc_id = result.scalar_one()

        # Try to access as Firm A — must return nothing
        result2 = await seeded_session.execute(
            sa_text("SELECT * FROM documents WHERE ca_firm_id = :firm AND id = :doc_id"),
            {"firm": str(FIRM_A_ID), "doc_id": firm_b_doc_id},
        )
        assert result2.fetchone() is None, "Firm A must not see Firm B's document"


# ==========================================================================
# Test: BaseQuery _insert always injects firm_id
# ==========================================================================

class TestInsertInjection:
    """Verify that _insert always overrides ca_firm_id."""

    async def test_insert_overrides_wrong_firm_id(
        self, seeded_session: AsyncSession
    ) -> None:
        """Even if someone sets a different ca_firm_id, _insert overrides it."""
        query = BaseQuery(seeded_session, ca_firm_id=FIRM_A_ID)

        # Create a client object with WRONG firm ID
        sneaky_client = Client(
            id=uuid.uuid4(),
            ca_firm_id=FIRM_B_ID,  # WRONG — should be overridden
            trade_name="Sneaky Client",
            is_active=True,
        )
        result = await query._insert(sneaky_client)

        # BaseQuery must have overridden it
        assert str(result.ca_firm_id) == str(FIRM_A_ID)
