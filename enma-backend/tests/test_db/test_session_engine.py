"""Engine-construction regression tests.

Bug 1 (ENMA-BACKEND-8) — ``DuplicatePreparedStatementError`` from asyncpg
fired ~21x/day from ``cron_task_heartbeat`` because Supavisor's
transaction-mode pooler rebinds physical connections across client
sessions, so asyncpg's per-connection prepared-statement cache collides
on checkout. Canonical fix: ``statement_cache_size=0`` on every asyncpg
``connect_args``.

These tests pin the engine config so a future refactor cannot silently
re-introduce the cache.
"""

from __future__ import annotations

import importlib

import app.db.session as session_module
from app.config import settings


def _reset_engine_singletons() -> None:
    session_module._engine = None
    session_module._sessionmaker = None

def test_engine_disables_asyncpg_statement_cache(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Both transaction-mode and session-mode engines must pin ``statement_cache_size=0``."""
    _reset_engine_singletons()
    importlib.reload(session_module)

    captured: dict[str, object] = {}

    def _fake_create_async_engine(url: str, **kwargs: object):  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(session_module, "create_async_engine", _fake_create_async_engine)

    session_module._build_engine()
    connect_args = captured["kwargs"]["connect_args"]  # type: ignore[index]
    assert isinstance(connect_args, dict)
    assert connect_args.get("statement_cache_size") == 0, (
        "asyncpg statement_cache_size MUST be 0 to survive Supavisor "
        "transaction-mode connection rebinding (Bug 1 / ENMA-BACKEND-8)."
    )
    assert "server_settings" in connect_args


def test_engine_transaction_mode_uses_nullpool(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Transaction-mode (port 6543) connections must NOT pool on the SQLAlchemy side."""
    from sqlalchemy import NullPool

    _reset_engine_singletons()

    captured: dict[str, object] = {}

    def _fake_create_async_engine(url: str, **kwargs: object):  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(session_module, "create_async_engine", _fake_create_async_engine)
    monkeypatch.setattr(settings, "db_pool_size", 0)
    session_module._build_engine()
    assert captured["kwargs"].get("poolclass") is NullPool  # type: ignore[union-attr]
