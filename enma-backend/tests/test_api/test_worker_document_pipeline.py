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

    class _FakeDocumentQuery:
        """W3.5-d: early-dedup check defaults to MISS in tests so the
        existing pipeline path runs. Individual tests can override by
        monkeypatching find_by_source_file_hash on the instance.
        """

        def __init__(self, **_kw: Any) -> None: ...

        async def find_by_source_file_hash(
            self, *, source_file_hash: str
        ) -> Any:
            return None

    monkeypatch.setattr(worker_module, "DocumentQuery", _FakeDocumentQuery)

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

    # R2 — the worker now calls extract_only + finalize_document directly
    # instead of running the entire run_pipeline at once. Patch both for
    # tests that drive _run_document_pipeline through the new code path.
    from app.agents.pipeline import ExtractionOutcome

    async def fake_extract_only(_file_bytes: bytes) -> ExtractionOutcome:
        if pipeline_raise is not None and isinstance(pipeline_raise, PipelineError):
            raise pipeline_raise
        clean = _clean_result()
        return ExtractionOutcome(
            document_type=clean.document_type,
            extraction=clean.extraction,
            verification=clean.verification,
            stages=clean.stages,
        )

    monkeypatch.setattr(worker_module, "extract_only", fake_extract_only)

    async def fake_finalize(**kwargs: Any) -> PipelineResult:
        captures["pipeline_calls"].append(kwargs)
        if pipeline_raise is not None and not isinstance(pipeline_raise, PipelineError):
            raise pipeline_raise
        return pipeline_result or _clean_result()

    monkeypatch.setattr(worker_module, "finalize_document", fake_finalize)
    return captures


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class _FakeFirm:
    id = uuid.uuid4()
    primary_channel = "telegram"  # W4-P1 default — factory picks TelegramClient


class _FakeClient:
    id = uuid.uuid4()
    trade_name = "FakeClient Pvt Ltd"
    legal_name: str | None = None
    gstin = "27AABCU9603R1ZN"
    is_active = True
    gst_tds_deductor = False


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


# ---------------------------------------------------------------------------
# Refactor B — resume pipeline after /assign
# ---------------------------------------------------------------------------


class _FakePendingRow:
    """In-memory stand-in for a resolved pending_assignments row."""

    def __init__(
        self,
        *,
        file_ids: list[str],
        message_id: int | None,
        resolved_client_id: uuid.UUID,
        extraction: dict[str, Any] | None = None,
        extraction_document_type: str | None = None,
    ) -> None:
        self.file_ids = file_ids
        self.message_id = message_id
        self.resolved_client_id = resolved_client_id
        self.extraction = extraction
        self.extraction_document_type = extraction_document_type


@pytest.mark.asyncio
async def test_resume_after_assign_fires_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refactor B regression: ``/assign`` → resume pipeline → summary sent.

    Reproduces the screenshot bug from prod: previously, ``/assign`` resolved
    the pending_assignment row but never ran ``run_pipeline``, leaving the
    queued document orphaned. Now the helper must fire extraction for every
    file_id in the row and reply-to the ORIGINAL document message_id.
    """
    captures = _patch_common(monkeypatch, firm=_FakeFirm(), client=_FakeClient())

    fake_row = _FakePendingRow(
        file_ids=["tg-file-7", "tg-file-8"],
        message_id=42,  # the original upload's message_id
        resolved_client_id=uuid.uuid4(),
    )

    class _FakePendingQuery:
        def __init__(self, **_kw: Any) -> None: ...

        def _scoped_select(self, _model: Any) -> Any:
            class _Stmt:
                def where(self, *_a: Any, **_kw: Any) -> _Stmt:
                    return self

            return _Stmt()

    monkeypatch.setattr(worker_module, "PendingAssignmentQuery", _FakePendingQuery)

    # Patch the AsyncSession.execute call to return our fake row.
    class _FakeResult:
        def scalar_one_or_none(self) -> Any:
            return fake_row

    async def fake_execute(_stmt: Any) -> _FakeResult:
        return _FakeResult()

    # The session is a _FakeSession instance; attach execute() to it.
    original_factory = worker_module.get_sessionmaker

    @asynccontextmanager
    async def session_with_execute():
        s = _FakeSession()
        s.execute = fake_execute  # type: ignore[attr-defined]
        yield s

    def factory_returning_executable():
        return session_with_execute

    monkeypatch.setattr(worker_module, "get_sessionmaker", factory_returning_executable)

    pending_id = uuid.uuid4()
    async with session_with_execute() as session:
        await worker_module._resume_pipeline_for_pending_assignment(
            session=session,
            firm=_FakeFirm(),
            chat_id=999,
            pending_assignment_id=pending_id,
        )

    # Both queued files were downloaded.
    assert captures["downloads"] == ["tg-file-7", "tg-file-8"]
    # Pipeline ran for each.
    assert len(captures["pipeline_calls"]) == 2
    # Two summaries sent, each threaded onto the ORIGINAL message_id (42),
    # not the /assign reply.
    assert len(captures["sends"]) == 2
    for send in captures["sends"]:
        assert send["chat_id"] == 999
        assert send["reply_to_message_id"] == 42
        assert "Document processed" in send["html_text"]

    # Restore so subsequent tests aren't poisoned.
    monkeypatch.setattr(worker_module, "get_sessionmaker", original_factory)


@pytest.mark.asyncio
async def test_resume_with_missing_row_logs_and_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the pending row was already deleted, resume is a no-op (no crash)."""
    captures = _patch_common(monkeypatch, firm=_FakeFirm(), client=_FakeClient())

    class _FakePendingQuery:
        def __init__(self, **_kw: Any) -> None: ...

        def _scoped_select(self, _model: Any) -> Any:
            class _Stmt:
                def where(self, *_a: Any, **_kw: Any) -> _Stmt:
                    return self

            return _Stmt()

    monkeypatch.setattr(worker_module, "PendingAssignmentQuery", _FakePendingQuery)

    class _FakeResult:
        def scalar_one_or_none(self) -> Any:
            return None

    async def fake_execute(_stmt: Any) -> _FakeResult:
        return _FakeResult()

    session = _FakeSession()
    session.execute = fake_execute  # type: ignore[attr-defined]
    await worker_module._resume_pipeline_for_pending_assignment(
        session=session,
        firm=_FakeFirm(),
        chat_id=999,
        pending_assignment_id=uuid.uuid4(),
    )
    # No pipeline ran, no message sent — pure no-op on missing/already-gone row.
    assert captures["downloads"] == []
    assert captures["pipeline_calls"] == []
    assert captures["sends"] == []


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
    # R2: extract_only now runs BEFORE identity resolution. A PipelineError
    # raised by the extraction half surfaces as a "couldn't read" warm
    # error and the routing/persistence half never runs — so pipeline_calls
    # (which records finalize_document calls) is empty by design.
    assert len(captures["sends"]) == 1
    assert "read this document" in captures["sends"][0]["html_text"]


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


# ---------------------------------------------------------------------------
# Refactor R2 — extract-first autonomous routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r2_pending_assignment_prompt_includes_extracted_buyer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2: the ambiguity prompt must show extracted buyer name + GSTIN.

    Previously the prompt just said "Which client?" with no context.
    R2's extract-first flow has the buyer details in hand by the time
    we prompt, so the CA can confirm at a glance.
    """
    captures = _patch_common(
        monkeypatch,
        firm=_FakeFirm(),
        client=_FakeClient(),
        pending_assignment=True,
    )
    await worker_module._run_document_pipeline(_envelope())
    # No pipeline persist call — the doc went to EXPLICIT_ASK.
    assert captures["pipeline_calls"] == []
    # One send: the informed prompt.
    assert len(captures["sends"]) == 1
    html = captures["sends"][0]["html_text"]
    # The stub's _clean_result() carries buyer name 'B' and a 27... GSTIN.
    assert "Which client" in html
    # Extracted buyer GSTIN surfaces verbatim in the prompt body.
    assert "27AABCU9603R1ZN" in html
    # R4 — vendor GSTIN must also surface so the CA can route an
    # outward-supply invoice without re-reading the PDF.
    assert "29AAAGU0010P1Z5" in html
    assert "Vendor:" in html
    assert "Buyer:" in html


@pytest.mark.asyncio
async def test_r2_resume_uses_cached_extraction_no_reextract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2: /assign on a pending row with cached extraction must skip OCR.

    The pending_assignments.extraction column carries the extract_only
    output. The resume helper reconstructs an ExtractionOutcome from it
    and calls finalize_document directly — telegram.download_file is
    NEVER invoked, and the LLM is NEVER hit a second time.
    """
    captures = _patch_common(monkeypatch, firm=_FakeFirm(), client=_FakeClient())

    cached_extraction = {
        "vendor": {"name": "V", "gstin": "29AAAGU0010P1Z5"},
        "buyer": {"name": "B", "gstin": "27AABCU9603R1ZN"},
        "totals": {"grand_total": "1180.00"},
        "line_items": [{}],
    }
    fake_row = _FakePendingRow(
        file_ids=["tg-file-cached"],
        message_id=42,
        resolved_client_id=uuid.uuid4(),
        extraction=cached_extraction,
        extraction_document_type="B2B_INVOICE",
    )

    class _FakePendingQuery:
        def __init__(self, **_kw: Any) -> None: ...

        def _scoped_select(self, _model: Any) -> Any:
            class _Stmt:
                def where(self, *_a: Any, **_kw: Any) -> _Stmt:
                    return self

            return _Stmt()

    monkeypatch.setattr(worker_module, "PendingAssignmentQuery", _FakePendingQuery)

    class _FakeResult:
        def scalar_one_or_none(self) -> Any:
            return fake_row

    async def fake_execute(_stmt: Any) -> _FakeResult:
        return _FakeResult()

    @asynccontextmanager
    async def session_with_execute():
        s = _FakeSession()
        s.execute = fake_execute  # type: ignore[attr-defined]
        yield s

    async with session_with_execute() as session:
        await worker_module._resume_pipeline_for_pending_assignment(
            session=session,
            firm=_FakeFirm(),
            chat_id=999,
            pending_assignment_id=uuid.uuid4(),
        )

    # Crucial: zero downloads, zero extract_only calls. finalize_document
    # ran exactly once with the cached extraction.
    assert captures["downloads"] == []
    assert len(captures["pipeline_calls"]) == 1
    finalize_kwargs = captures["pipeline_calls"][0]
    assert finalize_kwargs["outcome"].extraction == cached_extraction
    assert finalize_kwargs["outcome"].document_type == "B2B_INVOICE"
    # One summary sent, threaded onto the original document message_id.
    assert len(captures["sends"]) == 1
    assert captures["sends"][0]["reply_to_message_id"] == 42


@pytest.mark.asyncio
async def test_early_dedup_skips_pipeline_and_sends_banner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W3.5-d regression: a re-upload (same file bytes) must short-circuit
    the pipeline BEFORE extract_only runs. Production cost ~$0.013 +
    63s per re-upload before this fix (request 69227d42, 2026-06-16).
    """
    captures = _patch_common(monkeypatch, firm=_FakeFirm(), client=_FakeClient())

    existing_id = uuid.uuid4()

    class _DupDocumentQuery:
        def __init__(self, **_kw: Any) -> None: ...

        async def find_by_source_file_hash(
            self, *, source_file_hash: str
        ) -> Any:
            existing = _FakeFirm()  # any object with .id is fine
            existing.id = existing_id
            return existing

    monkeypatch.setattr(worker_module, "DocumentQuery", _DupDocumentQuery)

    await worker_module._run_document_pipeline(_envelope())

    # Zero extract calls — the whole point of the early-dedup fix.
    assert captures["pipeline_calls"] == []
    # One banner message sent to the user with the existing doc's ref.
    assert len(captures["sends"]) == 1
    body = captures["sends"][0]["html_text"]
    assert "Re-upload detected" in body
    assert str(existing_id)[:8] in body
