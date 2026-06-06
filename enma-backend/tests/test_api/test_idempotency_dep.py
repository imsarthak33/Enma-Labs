"""Tests for the check_idempotency dependency.

We mock the underlying query so we don't need Postgres. The session yielded
by the fixture is a stub from ``conftest.fake_session_dep``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from app.api.middleware.envelope_verify import ENVELOPE_VERSION, DecodedEnvelope
from app.api.middleware.idempotency import check_idempotency
from app.db.queries import idempotency as idem_queries


def _envelope(*, chat_id: int | None = 1, message_id: int | None = 2) -> DecodedEnvelope:
    return DecodedEnvelope(
        v=ENVELOPE_VERSION,
        kind="document",
        issued_at=datetime.now(UTC),
        nonce="n",
        chat_id=chat_id,
        message_id=message_id,
        update_id=3,
        payload={"k": "v"},
    )


class _StubSession:
    """Stand-in AsyncSession with the bare surface ``insert_if_new`` needs."""

    async def execute(self, *_a: object, **_kw: object) -> object: ...
    async def commit(self) -> None: ...


@pytest.mark.asyncio
async def test_new_envelope_returns_log_id(monkeypatch: pytest.MonkeyPatch) -> None:
    new_id = uuid.uuid4()

    async def fake_insert(*_args: Any, **_kwargs: Any) -> uuid.UUID:
        return new_id

    monkeypatch.setattr(idem_queries, "insert_if_new", fake_insert)
    verdict = await check_idempotency(_envelope(), _StubSession())  # type: ignore[arg-type]
    assert verdict.is_duplicate is False
    assert verdict.log_id == new_id


@pytest.mark.asyncio
async def test_duplicate_returns_no_log_id(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_insert(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(idem_queries, "insert_if_new", fake_insert)
    verdict = await check_idempotency(_envelope(), _StubSession())  # type: ignore[arg-type]
    assert verdict.is_duplicate is True
    assert verdict.log_id is None


@pytest.mark.asyncio
async def test_envelope_without_message_id_is_not_deduped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def fake_insert(*_args: Any, **_kwargs: Any) -> uuid.UUID:
        nonlocal called
        called = True
        return uuid.uuid4()

    monkeypatch.setattr(idem_queries, "insert_if_new", fake_insert)
    verdict = await check_idempotency(
        _envelope(message_id=None),
        _StubSession(),  # type: ignore[arg-type]
    )
    assert verdict.is_duplicate is False
    assert verdict.log_id is None
    assert called is False


def test_hash_payload_is_deterministic_and_hex() -> None:
    h1 = idem_queries.hash_payload("payload-1")
    h2 = idem_queries.hash_payload("payload-1")
    assert h1 == h2
    assert len(h1) == 64
    int(h1, 16)  # raises if not hex
