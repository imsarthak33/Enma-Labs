"""R3 — agentic routing reply: free-form text resolves a pending assignment.

These tests exercise :func:`app.api.routes.worker._try_resolve_pending_from_text`
directly. We monkey-patch the two query classes and the downstream
pipeline-resume helper so the test runs without a DB or Telegram.

The R3 contract:

* No open pending row → returns ``False`` (caller falls through to
  slash / supervisor).
* Open pending + a single HIGH (≥ 0.85) fuzzy match → resolve, fire
  the cached-extraction finalize, return ``True``.
* Open pending + ambiguous (multiple ≥ 0.55 with no clear winner) →
  send a disambiguation prompt, return ``True``.
* Open pending + no fuzzy match → returns ``False`` (let supervisor try).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.api.routes import worker as worker_module
from app.services import telegram as telegram_service


class _FakeFirm:
    id = uuid.uuid4()


class _FakeClient:
    def __init__(self, trade_name: str) -> None:
        self.id = uuid.uuid4()
        self.trade_name = trade_name


class _FakePending:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.message_id = 42


def _patch_telegram(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sends: list[dict[str, Any]] = []

    async def fake_send(**kwargs: Any) -> dict[str, Any]:
        sends.append(kwargs)
        return {"message_id": 1}

    monkeypatch.setattr(telegram_service, "send_message", fake_send)
    return sends


def _patch_pending_query(
    monkeypatch: pytest.MonkeyPatch,
    *,
    open_rows: list[_FakePending],
    resolve_returns: Any = "self",
) -> dict[str, Any]:
    captures: dict[str, Any] = {"resolved": []}

    class _FakePendingQuery:
        def __init__(self, **_kw: Any) -> None: ...

        async def list_open_for_chat(self, *, chat_id: int) -> list[_FakePending]:
            return open_rows

        async def resolve(self, *, pending_id: uuid.UUID, client_id: uuid.UUID) -> Any:
            captures["resolved"].append((pending_id, client_id))
            if resolve_returns == "self":
                return open_rows[0] if open_rows else None
            return resolve_returns

    monkeypatch.setattr(worker_module, "PendingAssignmentQuery", _FakePendingQuery)
    return captures


def _patch_client_query(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ranked: list[tuple[_FakeClient, float]],
) -> None:
    class _FakeClientQuery:
        def __init__(self, **_kw: Any) -> None: ...

        async def search_by_name_ranked(
            self,
            _name: str,
            *,
            min_similarity: float = 0.55,
            limit: int = 3,
        ) -> list[tuple[_FakeClient, float]]:
            return [r for r in ranked if r[1] >= min_similarity][:limit]

    monkeypatch.setattr(worker_module, "ClientQuery", _FakeClientQuery)


def _patch_session(monkeypatch: pytest.MonkeyPatch) -> Any:
    class _S:
        async def commit(self) -> None:
            return None

    return _S()


def _patch_resume(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    captures: list[Any] = []

    async def fake_resume(**kwargs: Any) -> None:
        captures.append(kwargs)

    monkeypatch.setattr(
        worker_module, "_resume_pipeline_for_pending_assignment", fake_resume
    )
    return captures


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_pending_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """No open pending_assignment → caller routes to supervisor as usual."""
    _patch_telegram(monkeypatch)
    _patch_pending_query(monkeypatch, open_rows=[])
    _patch_client_query(monkeypatch, ranked=[])
    _patch_resume(monkeypatch)
    session = _patch_session(monkeypatch)

    handled = await worker_module._try_resolve_pending_from_text(
        session=session,
        firm=_FakeFirm(),
        chat_id=999,
        reply_to_message_id=10,
        text="hello there",
    )
    assert handled is False


@pytest.mark.asyncio
async def test_high_confidence_match_resolves_and_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3 happy path: 'CLEIND' → 92% match → resolve + finalize, NO /assign needed."""
    sends = _patch_telegram(monkeypatch)
    pending = [_FakePending()]
    pending_captures = _patch_pending_query(monkeypatch, open_rows=pending)
    target = _FakeClient("CLEIND PRODUCT & SERVICE")
    _patch_client_query(monkeypatch, ranked=[(target, 0.92)])
    resume_captures = _patch_resume(monkeypatch)
    session = _patch_session(monkeypatch)

    handled = await worker_module._try_resolve_pending_from_text(
        session=session,
        firm=_FakeFirm(),
        chat_id=999,
        reply_to_message_id=10,
        text="CLEIND",
    )

    assert handled is True
    # The pending was resolved against the top candidate.
    assert len(pending_captures["resolved"]) == 1
    assert pending_captures["resolved"][0][1] == target.id
    # The resume helper was invoked (cached-extraction path).
    assert len(resume_captures) == 1
    assert resume_captures[0]["pending_assignment_id"] == pending[0].id
    # Confirmation message was sent.
    assert len(sends) == 1
    assert "Routed to" in sends[0]["html_text"]
    assert "CLEIND" in sends[0]["html_text"]


@pytest.mark.asyncio
async def test_two_high_candidates_too_close_disambiguates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two candidates within 0.05 similarity → disambiguate, no resolve."""
    sends = _patch_telegram(monkeypatch)
    pending = [_FakePending()]
    pending_captures = _patch_pending_query(monkeypatch, open_rows=pending)
    a = _FakeClient("FooCorp Pvt Ltd")
    b = _FakeClient("FooCorp Industries")
    _patch_client_query(monkeypatch, ranked=[(a, 0.88), (b, 0.87)])
    resume_captures = _patch_resume(monkeypatch)
    session = _patch_session(monkeypatch)

    handled = await worker_module._try_resolve_pending_from_text(
        session=session,
        firm=_FakeFirm(),
        chat_id=999,
        reply_to_message_id=10,
        text="foocorp",
    )

    assert handled is True  # we consumed the reply
    # But did NOT resolve and did NOT resume — sent a disambiguation prompt.
    assert pending_captures["resolved"] == []
    assert resume_captures == []
    assert len(sends) == 1
    assert "Did you mean" in sends[0]["html_text"]
    assert "FooCorp Pvt Ltd" in sends[0]["html_text"]
    assert "FooCorp Industries" in sends[0]["html_text"]


@pytest.mark.asyncio
async def test_medium_match_disambiguates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single MEDIUM (0.55-0.85) match → disambiguate, don't auto-resolve."""
    sends = _patch_telegram(monkeypatch)
    pending = [_FakePending()]
    pending_captures = _patch_pending_query(monkeypatch, open_rows=pending)
    only = _FakeClient("CLEIND PRODUCT & SERVICE")
    _patch_client_query(monkeypatch, ranked=[(only, 0.70)])
    resume_captures = _patch_resume(monkeypatch)
    session = _patch_session(monkeypatch)

    handled = await worker_module._try_resolve_pending_from_text(
        session=session,
        firm=_FakeFirm(),
        chat_id=999,
        reply_to_message_id=10,
        text="cleind",
    )

    assert handled is True
    assert pending_captures["resolved"] == []
    assert resume_captures == []
    assert "Did you mean" in sends[0]["html_text"]


@pytest.mark.asyncio
async def test_no_fuzzy_match_returns_false_supervisor_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Open pending + text that matches no client → supervisor takes over."""
    sends = _patch_telegram(monkeypatch)
    pending = [_FakePending()]
    pending_captures = _patch_pending_query(monkeypatch, open_rows=pending)
    _patch_client_query(monkeypatch, ranked=[])  # nothing crossed 0.55
    resume_captures = _patch_resume(monkeypatch)
    session = _patch_session(monkeypatch)

    handled = await worker_module._try_resolve_pending_from_text(
        session=session,
        firm=_FakeFirm(),
        chat_id=999,
        reply_to_message_id=10,
        text="how does GST TDS work?",
    )

    assert handled is False
    assert pending_captures["resolved"] == []
    assert resume_captures == []
    assert sends == []  # no message — supervisor will reply
