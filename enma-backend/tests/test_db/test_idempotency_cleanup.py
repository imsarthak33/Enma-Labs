"""Tests for the idempotency_log cleanup helper (spec §2.4)."""

from __future__ import annotations

import pytest
from app.db.queries.idempotency import (
    DEFAULT_CLEANUP_AGE_HOURS,
    cleanup_idempotency_log,
)
from sqlalchemy.ext.asyncio import AsyncSession


class _FakeResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount
        self.executed: list[tuple[str, dict[str, object]]] = []
        self.commits = 0

    async def execute(self, stmt: object, params: dict[str, object]) -> _FakeResult:
        self.executed.append((str(stmt), params))
        return _FakeResult(self.rowcount)

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.asyncio
async def test_default_age_is_72_hours() -> None:
    assert DEFAULT_CLEANUP_AGE_HOURS == 72


@pytest.mark.asyncio
async def test_cleanup_runs_and_commits() -> None:
    session = _FakeSession(rowcount=17)
    deleted = await cleanup_idempotency_log(
        session=session,  # type: ignore[arg-type]
        max_age_hours=12,
    )
    assert deleted == 17
    assert session.commits == 1
    assert session.executed[0][1] == {"max_age_hours": 12}


@pytest.mark.asyncio
async def test_zero_rowcount_returns_zero() -> None:
    session = _FakeSession(rowcount=0)
    deleted = await cleanup_idempotency_log(session=session)  # type: ignore[arg-type]
    assert deleted == 0


@pytest.mark.asyncio
async def test_rejects_non_positive_window() -> None:
    session = _FakeSession(rowcount=0)
    with pytest.raises(ValueError, match="max_age_hours must be > 0"):
        await cleanup_idempotency_log(
            session=session,  # type: ignore[arg-type]
            max_age_hours=0,
        )


@pytest.mark.asyncio
async def test_session_type_is_async() -> None:
    # Documentation guard — the helper signature must accept AsyncSession.
    # ``from __future__ import annotations`` defers annotation evaluation,
    # so resolve via typing.get_type_hints.
    from typing import get_type_hints

    hints = get_type_hints(cleanup_idempotency_log)
    assert hints["session"] is AsyncSession
