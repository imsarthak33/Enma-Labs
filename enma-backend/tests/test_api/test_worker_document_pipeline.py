"""Tests for the Phase 4/6 ``_run_document_pipeline`` background coroutine.

We hand it a decoded envelope and mock every external dependency:

  * ``find_firm_by_admin_chat_id`` — returns a fixture firm
  * ``resolve_identity``           — returns a synthetic ResolutionOutcome
  * ``ClientQuery.get_by_id``      — returns a fixture client
  * ``telegram.download_file``     — returns bytes
  * ``run_pipeline``               — returns a synthetic PipelineResult
  * ``telegram.send_message``      — captures calls

Sessions are stubbed via ``get_sessionmaker`` since we never run real SQL.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from app.agents.pipeline import (
    PipelineError,
    PipelineResult,
    PipelineStage,
    PipelineStageOutcome,
    PipelineStageStatus,
)
from app.agents.verifier import VerificationResult
from app.api.middleware.envelope_verify import ENVELOPE_VERSION, DecodedEnvelope
from app.api.routes import worker as worker_module
from app.services import telegram as telegram_service


def _envelope(*, file_id: str | None = "tg-file-1") -> DecodedEnvelope:
    payload: dict[str, Any] = {}
    if file_id is not None:
        payload["file_id"] = file_id
    return DecodedEnvelope(
        v=ENVELOPE_VERSION,
        kind="document",
        issued_at=datetime.now(UTC),
        nonce="n",
        chat_id=999,
        message_id=42,
        update_id=7,
        payload=payload,
    )


class _FakeSession:
    """Minimal AsyncSession stand-in supporting ``async with`` semantics."""

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_a: object) -> None:
        return None

    async def close(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _fake_sessionmaker():
    @asynccontextmanager
    async def factory():
        yield _FakeSession()

    return factory


def _stage_ok(stage: PipelineStage) -> PipelineStageOutcome:
    return PipelineStageOutcome(stage=stage, status=PipelineStageStatus.OK, duration_ms=1)


def _clean_result(doc_id: uuid.UUID | None = None) -> PipelineResult:
    return PipelineResult(
        document_id=doc_id or uuid.uuid4(),
        document_type="B2B_INVOICE",
        extraction={
            "vendor": {"name": "V", "gstin": "29AAAGU0010P1Z5"},
            "buyer": {"name": "B", "gstin": "27AABCU9603R1ZN"},
            "totals": {"grand_total": "1180.00"},
            "line_items": [{}],
        },
        verification=VerificationResult(issues=()),
        stages=tuple(_stage_ok(s) for s in PipelineStage),
    )


_SENTINEL = object()


def _patch_common(
    monkeypatch: pytest.MonkeyPatch,
    *,
    firm: Any = _SENTINEL,
    client: Any = _SENTINEL,
    download_bytes: bytes = b"\x89PNG\r\n\x1a\nfake",
    pipeline_result: PipelineResult | None = None,
    pipeline_raise: Exception | None = None,
    pending_assignment: bool = False,
) -> dict[str, list[Any]]:
    """Apply standard monkeypatches and return capture buckets."""
    captures: dict[str, list[Any]] = {
        "downloads": [],
        "sends": [],
        "pipeline_calls": [],
    }

    async def fake_find_firm(_session: Any, _chat_id: int) -> Any:
        return firm

    monkeypatch.setattr(worker_module, "find_firm_by_admin_chat_id", fake_find_firm)

    from app.identity.resolver import (
        ResolutionConfidence,
        ResolutionOutcome,
        ResolutionStage,
    )

    # Synthetic client_id so is_resolved=True even when the fixture
    # client object is None (drives the "routed client no longer exists" path).
    synthetic_client_id = getattr(client, "id", None) or uuid.uuid4()

    async def fake_resolve_identity(**_kw: Any) -> ResolutionOutcome:
        if pending_assignment:
            return ResolutionOutcome(
                stage=ResolutionStage.EXPLICIT_ASK,
                confidence=ResolutionConfidence.EXPLICIT,
                client_id=None,
                pending_assignment_id=uuid.uuid4(),
                resolution_time_ms=1,
                log_id=uuid.uuid4(),
            )
        return ResolutionOutcome(
            stage=ResolutionStage.SESSION,
            confidence=ResolutionConfidence.HIGH,
            client_id=synthetic_client_id,
            pending_assignment_id=None,
            resolution_time_ms=1,
            log_id=uuid.uuid4(),
        )

    monkeypatch.setattr(worker_module, "resolve_identity", fake_resolve_identity)

    class _FakeClientQuery:
        def __init__(self, **_kw: Any) -> None: ...

        async def first_active(self) -> Any:
            return client

        async def get_by_id(self, _client_id: Any) -> Any:
            return client

        async def list_active(self) -> list[Any]:
            return [client] if client is not None else []

    monkeypatch.setattr(worker_module, "ClientQuery", _FakeClientQuery)
    monkeypatch.setattr(worker_module, "get_sessionmaker", _fake_sessionmaker)

    async def fake_download(_file_id: str) -> bytes:
        captures["downloads"].append(_file_id)
        return download_bytes

    monkeypatch.setattr(telegram_service, "download_file", fake_download)

    async def fake_send(**kwargs: Any) -> dict[str, Any]:
        captures["sends"].append(kwargs)
        return {"message_id": 1}

    monkeypatch.setattr(telegram_service, "send_message", fake_send)

    async def fake_pipeline(**kwargs: Any) -> PipelineResult:
        captures["pipeline_calls"].append(kwargs)
        if pipeline_raise is not None:
            raise pipeline_raise
        return pipeline_result or _clean_result()

    monkeypatch.setattr(worker_module, "run_pipeline", fake_pipeline)
    return captures


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class _FakeFirm:
    id = uuid.uuid4()


class _FakeClient:
    id = uuid.uuid4()


@pytest.mark.asyncio
async def test_happy_path_sends_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    captures = _patch_common(monkeypatch, firm=_FakeFirm(), client=_FakeClient())
    await worker_module._run_document_pipeline(_envelope())
    assert captures["downloads"] == ["tg-file-1"]
    assert len(captures["pipeline_calls"]) == 1
    # Two messages may be sent in general; here just the summary.
    assert len(captures["sends"]) == 1
    sent = captures["sends"][0]
    assert sent["chat_id"] == 999
    assert "Document processed" in sent["html_text"]


@pytest.mark.asyncio
async def test_no_firm_sends_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    captures = _patch_common(monkeypatch, firm=None, client=_FakeClient())
    await worker_module._run_document_pipeline(_envelope())
    # No pipeline invocation, no download.
    assert captures["downloads"] == []
    assert captures["pipeline_calls"] == []
    # An HTML error was sent.
    assert len(captures["sends"]) == 1
    assert "No firm is registered" in captures["sends"][0]["html_text"]


@pytest.mark.asyncio
async def test_resolved_client_missing_sends_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity resolved to a client_id but the row no longer exists."""
    captures = _patch_common(monkeypatch, firm=_FakeFirm(), client=None)
    await worker_module._run_document_pipeline(_envelope())
    assert captures["pipeline_calls"] == []
    assert "Routed client no longer exists" in captures["sends"][0]["html_text"]


@pytest.mark.asyncio
async def test_pending_assignment_no_clients_sends_no_clients_msg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity cascade hits stage 5 but the firm has no active clients."""
    captures = _patch_common(
        monkeypatch, firm=_FakeFirm(), client=None, pending_assignment=True
    )
    await worker_module._run_document_pipeline(_envelope())
    assert captures["pipeline_calls"] == []
    assert "no active clients" in captures["sends"][0]["html_text"]


@pytest.mark.asyncio
async def test_missing_file_id_sends_error(monkeypatch: pytest.MonkeyPatch) -> None:
    captures = _patch_common(monkeypatch, firm=_FakeFirm(), client=_FakeClient())
    await worker_module._run_document_pipeline(_envelope(file_id=None))
    assert captures["downloads"] == []
    assert "Could not find a file" in captures["sends"][0]["html_text"]


@pytest.mark.asyncio
async def test_download_failure_sends_error(monkeypatch: pytest.MonkeyPatch) -> None:
    captures = _patch_common(monkeypatch, firm=_FakeFirm(), client=_FakeClient())

    async def boom(_file_id: str) -> bytes:
        raise telegram_service.TelegramAPIError("getFile", 500, "down")

    monkeypatch.setattr(telegram_service, "download_file", boom)
    await worker_module._run_document_pipeline(_envelope())
    assert captures["pipeline_calls"] == []
    assert "download" in captures["sends"][0]["html_text"].lower()


@pytest.mark.asyncio
async def test_pipeline_error_sends_error(monkeypatch: pytest.MonkeyPatch) -> None:
    captures = _patch_common(
        monkeypatch,
        firm=_FakeFirm(),
        client=_FakeClient(),
        pipeline_raise=PipelineError("file processing failed"),
    )
    await worker_module._run_document_pipeline(_envelope())
    assert captures["pipeline_calls"]  # was attempted
    # Single error message sent (no summary).
    assert len(captures["sends"]) == 1
    assert "Document processing failed" in captures["sends"][0]["html_text"]


@pytest.mark.asyncio
async def test_no_chat_id_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    captures = _patch_common(monkeypatch, firm=_FakeFirm(), client=_FakeClient())
    no_chat = DecodedEnvelope(
        v=ENVELOPE_VERSION,
        kind="document",
        issued_at=datetime.now(UTC),
        nonce="n",
        chat_id=None,
        message_id=None,
        update_id=None,
        payload={"file_id": "x"},
    )
    await worker_module._run_document_pipeline(no_chat)
    # Nothing happens — no firm lookup, no pipeline call, no send.
    assert captures["downloads"] == []
    assert captures["pipeline_calls"] == []
    assert captures["sends"] == []
