"""Shared pytest fixtures.

Phase 0 uses an in-memory dependency override for the DB session so the
basic test suite runs without a live Postgres. Phase 1 introduces
testcontainers-based integration tests; those will be marked
``@pytest.mark.integration`` and skip in fast CI runs.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from fastapi import FastAPI

# Force a 'test' env before importing the app so config validation runs in
# a known state. Must happen before any ``from app...`` import below.
os.environ.setdefault("ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://enma:dev_password@localhost:5432/enma_test",
)


@pytest.fixture(scope="session")
def app_instance() -> FastAPI:
    """Return a freshly built FastAPI instance for the test session."""
    from app.main import create_app

    return create_app()


@pytest.fixture()
def client(app_instance: FastAPI) -> Iterator[TestClient]:
    """A synchronous TestClient that exercises the full middleware stack."""
    with TestClient(app_instance) as c:
        yield c


@pytest.fixture()
async def fake_session_dep(app_instance: FastAPI) -> AsyncIterator[None]:
    """Override ``get_session`` with a no-op stub for tests that don't need a DB."""
    from app.db.session import get_session

    class _StubSession:
        async def execute(self, *_a: object, **_kw: object) -> object:
            class _Result:
                def scalar_one(self) -> int:
                    return 1

            return _Result()

        async def close(self) -> None:
            return None

    async def _override() -> AsyncIterator[_StubSession]:
        yield _StubSession()

    app_instance.dependency_overrides[get_session] = _override
    try:
        yield
    finally:
        app_instance.dependency_overrides.pop(get_session, None)
