"""SQLAlchemy async engine and session factory — Supabase-aware.

Engine is a process-singleton, lazily constructed on first use. Session
factory yields ``AsyncSession`` instances suitable for FastAPI dependency
injection.

Supabase connection modes
-------------------------
Supabase exposes two connection endpoints:

  Session mode    (port 5432)
    Standard persistent connections. Supports ``SET`` commands.
    Use for EKS pods / long-lived processes.
    SQLAlchemy pool_size > 0 is fine here.

  Transaction mode (port 6543, via Supavisor)
    Stateless — connection returned to the pool after each transaction.
    Does NOT support ``SET`` / ``RESET`` (session-level commands).
    Required for AWS Fargate (no persistent connections).
    SQLAlchemy MUST use NullPool (pool_size=0) — Supavisor pools externally.

RLS firm-ID injection
---------------------
Row-Level Security policies check ``current_setting('app.current_firm_id')``.

  Session mode   : ``SET LOCAL app.current_firm_id = '<uuid>'`` works fine.
  Transaction mode: ``SET LOCAL`` is not supported. Instead, the firm_id is
                    passed as a Supabase request header or via a dedicated
                    function call at the start of each transaction. The
                    ``set_firm_context`` helper handles both cases.

Phase notes
-----------
Phase 0: Engine built here; only powers the liveness probe.
Phase 1: BaseQuery uses the session; RLS context set per-request.
Phase 3+: Worker routes call ``set_firm_context(session, firm_id)`` before
          any business query so RLS is always active.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any, Final

from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None

# Supabase requires the application_name to identify connections in pg_stat_activity.
# In transaction mode this travels with every connection checkout.
_ASYNCPG_SERVER_SETTINGS: Final[dict[str, str]] = {
    "application_name": "enma-backend",
}


def _build_engine() -> AsyncEngine:
    """Build the async engine with the correct pool strategy for Supabase.

    Transaction mode (port 6543): NullPool — Supavisor handles pooling.
    Session mode (port 5432):     QueuePool with configured size.

    asyncpg prepared-statement cache
    --------------------------------
    Supavisor's transaction-pooling mode rebinds physical Postgres
    connections to different client sessions between transactions. Any
    server-side prepared statements that asyncpg cached for the previous
    client (under names like ``__asyncpg_stmt_11__``) then collide on the
    next checkout, surfacing as
    ``DuplicatePreparedStatementError: prepared statement
    "__asyncpg_stmt_NN__" already exists``.

    The canonical Supabase + asyncpg + SQLAlchemy fix is to disable the
    asyncpg prepared-statement cache entirely (``statement_cache_size=0``)
    and unique-ify any remaining server-side prepares via
    ``prepared_statement_name_func`` — required even when the cache is off,
    because some driver paths still issue ``PREPARE`` once.
    """
    url = str(settings.database_url)

    asyncpg_connect_args: dict[str, Any] = {
        "server_settings": _ASYNCPG_SERVER_SETTINGS,
        # Disable client-side prepared-statement caching — pooled connections
        # are not stable identity carriers under Supavisor transaction mode.
        "statement_cache_size": 0,
    }

    common_kwargs: dict[str, Any] = {
        "echo": settings.db_echo,
        "future": True,
        "connect_args": asyncpg_connect_args,
    }

    if settings.uses_transaction_pooling() or settings.db_pool_size == 0:
        # AWS Fargate / transaction mode: disable SQLAlchemy pooling entirely.
        # pool_pre_ping is also disabled — Supavisor guarantees fresh conns.
        return create_async_engine(url, poolclass=NullPool, **common_kwargs)

    # Session mode (EKS / local dev): use a bounded connection pool.
    return create_async_engine(
        url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_s,
        pool_pre_ping=True,
        **common_kwargs,
    )


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first call."""
    global _engine  # noqa: PLW0603 — intentional process-singleton
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the cached async session factory."""
    global _sessionmaker  # noqa: PLW0603 — intentional process-singleton
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
            class_=AsyncSession,
        )
    return _sessionmaker


async def set_firm_context(session: AsyncSession, firm_id: uuid.UUID | str) -> None:
    """Set the RLS session variable for tenant isolation.

    Must be called at the start of every request that touches tenant-scoped
    tables. BaseQuery calls this automatically in Phase 3+ when a ca_firm_id
    is resolved.

    Session mode:     Issues ``SET LOCAL app.current_firm_id = '<uuid>'``.
    Transaction mode: Issues the same — Supabase supports it within a
                      transaction even on Supavisor (it resets on checkout).
    """
    await session.execute(
        text("SET LOCAL app.current_firm_id = :firm_id"),
        {"firm_id": str(firm_id)},
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield an ``AsyncSession`` with cleanup.

    Does NOT set firm context here — that requires a firm_id which is only
    known after authentication. Worker routes set firm context immediately
    after resolving the ca_firm_id from the inbound envelope.
    """
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def dispose_engine() -> None:
    """Tear down the engine on app shutdown."""
    global _engine, _sessionmaker  # noqa: PLW0603 — intentional process-singleton
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


__all__ = [
    "dispose_engine",
    "get_engine",
    "get_session",
    "get_sessionmaker",
    "set_firm_context",
]
