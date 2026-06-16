"""Pipeline orchestrator tests.

We monkey-patch the classifier and extractor (LLM-using) and exercise
the orchestration. Persistence is exercised via an in-memory SQLite
session with a minimal documents/clients/ca_firms DDL.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from app.agents import pipeline as pipeline_module
from app.agents.classifier import ClassificationResult
from app.agents.extractor import ExtractionResult
from app.agents.pipeline import (
    ExtractionOutcome,
    PipelineError,
    PipelineStage,
    PipelineStageStatus,
    extract_only,
    finalize_document,
    run_pipeline,
)
from pypdf import PdfWriter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Tiny PNG (1x1 transparent pixel) — enough to satisfy file kind detection.
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00\x01\x00"
    b"\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _make_pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# DB fixture — in-memory SQLite session shaped enough for DocumentQuery
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE ca_firms (id TEXT PRIMARY KEY, firm_name TEXT, "
                "admin_chat_id INTEGER, telegram_bot_token TEXT, "
                "subscription_tier TEXT DEFAULT 'starter', "
                "max_clients INTEGER DEFAULT 50, "
                "created_at TEXT, updated_at TEXT)"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE clients (id TEXT PRIMARY KEY, ca_firm_id TEXT, "
                "trade_name TEXT, legal_name TEXT, gstin TEXT, pan TEXT, "
                "state_code TEXT, address TEXT, contact_email TEXT, "
                "contact_phone TEXT, is_active INTEGER DEFAULT 1, "
                "gst_tds_deductor INTEGER DEFAULT 0, "
                "created_at TEXT, updated_at TEXT)"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE documents (id TEXT PRIMARY KEY, ca_firm_id TEXT, "
                "client_id TEXT, document_type TEXT, "
                "source_file_ids TEXT, extraction_data TEXT, "
                "tax_verdict TEXT, verification_result TEXT, "
                "filing_period_month INTEGER, filing_period_year INTEGER, "
                "processing_status TEXT DEFAULT 'pending', "
                "processing_time_ms INTEGER, content_hash TEXT, "
                "source_file_hash TEXT, "
                "created_at TEXT, updated_at TEXT)"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE filing_approvals (id TEXT PRIMARY KEY, "
                "ca_firm_id TEXT, filing_month INTEGER, filing_year INTEGER, "
                "approved_by_chat_id INTEGER, filing_snapshot TEXT, "
                "approval_hash TEXT, created_at TEXT)"
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    firm_id = uuid.uuid4()
    client_id = uuid.uuid4()
    now = datetime.now(UTC).isoformat()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO ca_firms (id, firm_name, admin_chat_id, "
                "telegram_bot_token, created_at, updated_at) "
                "VALUES (:id, 'F', 1, 't', :now, :now)"
            ),
            {"id": str(firm_id), "now": now},
        )
        await session.execute(
            text(
                "INSERT INTO clients (id, ca_firm_id, trade_name, "
                "created_at, updated_at) VALUES (:id, :firm, 'C', :now, :now)"
            ),
            {"id": str(client_id), "firm": str(firm_id), "now": now},
        )
        await session.commit()
        yield session, firm_id, client_id
    await engine.dispose()


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------


def _classify_stub(monkeypatch: pytest.MonkeyPatch, document_type: str = "B2B_INVOICE") -> None:
    async def fake_classify(_image_bytes: bytes) -> ClassificationResult:
        return ClassificationResult(
            document_type=document_type,
            confidence="HIGH",
            reasoning="stub",
        )

    monkeypatch.setattr(pipeline_module, "classify_document", fake_classify)


def _extract_stub(
    monkeypatch: pytest.MonkeyPatch,
    data: dict[str, Any] | None = None,
    *,
    raise_exc: Exception | None = None,
) -> None:
    payload = data or {
        "vendor": {"name": "V", "gstin": "29AAAGU0010P1Z5"},
        "buyer": {"name": "B", "gstin": "27AABCU9603R1ZN"},
        "invoice_number": "I-1",
        "invoice_date": "2026-04-01",
        "line_items": [
            {
                "description": "x",
                "taxable_value": "100.00",
                "cgst_rate": "9",
                "cgst_amount": "9.00",
                "sgst_rate": "9",
                "sgst_amount": "9.00",
            }
        ],
        "totals": {
            "taxable_value": "100.00",
            "total_cgst": "9.00",
            "total_sgst": "9.00",
            "total_igst": "0.00",
            "grand_total": "118.00",
        },
    }

    async def fake_extract(_image_bytes: bytes, *, document_type: str) -> ExtractionResult:
        if raise_exc is not None:
            raise raise_exc
        return ExtractionResult(
            document_type=document_type,
            data=payload,
            model_name="m",
            input_tokens=1,
            output_tokens=1,
        )

    monkeypatch.setattr(pipeline_module, "extract_document", fake_extract)


def _no_firm_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the pgvector-backed rule retrieval for SQLite tests.

    The pipeline now calls ``load_rules_for_engine`` which executes a
    ``cosine_distance`` query — unsupported on SQLite. Stub it to
    return an empty tuple so the deterministic engine still runs.
    """

    async def _empty(**_kwargs: Any) -> tuple[Any, ...]:
        return ()

    monkeypatch.setattr(pipeline_module, "load_rules_for_engine", _empty)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_image(monkeypatch: pytest.MonkeyPatch, db_session: Any) -> None:
    session, firm_id, client_id = db_session
    _classify_stub(monkeypatch)
    _extract_stub(monkeypatch)
    _no_firm_rules(monkeypatch)
    result = await run_pipeline(
        session=session,
        ca_firm_id=firm_id,
        client_id=client_id,
        file_bytes=_PNG_BYTES,
        source_file_ids=["tg-file-1"],
    )
    assert result.overall_status is PipelineStageStatus.OK
    assert result.document_type == "B2B_INVOICE"
    assert result.document_id is not None
    assert result.verification is not None
    # Stages all completed.
    statuses = {s.stage: s.status for s in result.stages}
    assert statuses[PipelineStage.FILE_PROCESSING] is PipelineStageStatus.OK
    assert statuses[PipelineStage.CLASSIFICATION] is PipelineStageStatus.OK
    assert statuses[PipelineStage.EXTRACTION] is PipelineStageStatus.OK
    assert statuses[PipelineStage.VERIFICATION] is PipelineStageStatus.OK
    assert statuses[PipelineStage.PERSISTENCE] is PipelineStageStatus.OK


@pytest.mark.asyncio
async def test_pdf_first_page_extracted(monkeypatch: pytest.MonkeyPatch, db_session: Any) -> None:
    session, firm_id, client_id = db_session
    _classify_stub(monkeypatch)
    _extract_stub(monkeypatch)
    _no_firm_rules(monkeypatch)
    result = await run_pipeline(
        session=session,
        ca_firm_id=firm_id,
        client_id=client_id,
        file_bytes=_make_pdf(3),
    )
    assert result.overall_status is PipelineStageStatus.OK


@pytest.mark.asyncio
async def test_pdf_input_classifier_receives_png_not_pdf(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Any,
) -> None:
    """Refactor I regression: PDFs must reach classifier/extractor as PNG.

    NVIDIA NIM rejects the OpenAI ``file`` content part. Before this fix,
    _prepare_image_bytes returned single-page PDF bytes to the classifier
    which then handed them to the LLM as a ``file`` part — surfaced in
    prod as HTTP 400 "data did not match any variant of untagged enum
    ChatCompletionRequestUserMessageContent". Now the pipeline rasterises
    the PDF page to PNG locally so the classifier/extractor see image
    bytes and use the ``image_url`` content part NIM accepts.
    """
    session, firm_id, client_id = db_session
    _no_firm_rules(monkeypatch)

    received_bytes: dict[str, bytes] = {}

    async def fake_classify(image_bytes: bytes) -> ClassificationResult:
        received_bytes["classifier"] = image_bytes
        return ClassificationResult(
            document_type="B2B_INVOICE",
            confidence="HIGH",
            reasoning="ok",
        )

    async def fake_extract(image_bytes: bytes, *, document_type: str) -> ExtractionResult:
        received_bytes["extractor"] = image_bytes
        return ExtractionResult(
            document_type=document_type,
            data={
                "vendor": {"name": "V", "gstin": None},
                "buyer": {"name": "B", "gstin": None},
                "totals": {"grand_total": "0.00"},
                "line_items": [],
            },
            model_name="m",
            input_tokens=1,
            output_tokens=1,
        )

    monkeypatch.setattr(pipeline_module, "classify_document", fake_classify)
    monkeypatch.setattr(pipeline_module, "extract_document", fake_extract)

    await run_pipeline(
        session=session,
        ca_firm_id=firm_id,
        client_id=client_id,
        file_bytes=_make_pdf(2),
    )

    # Both agents must have received PNG-magic bytes, not %PDF- bytes.
    assert received_bytes["classifier"].startswith(b"\x89PNG\r\n\x1a\n"), (
        "classifier received non-PNG bytes — PDF rasterisation regressed"
    )
    assert received_bytes["extractor"].startswith(b"\x89PNG\r\n\x1a\n"), (
        "extractor received non-PNG bytes — PDF rasterisation regressed"
    )


@pytest.mark.asyncio
async def test_unsupported_file_kind_raises(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    session, firm_id, client_id = db_session
    _classify_stub(monkeypatch)
    _extract_stub(monkeypatch)
    _no_firm_rules(monkeypatch)
    with pytest.raises(PipelineError, match="file processing"):
        await run_pipeline(
            session=session,
            ca_firm_id=firm_id,
            client_id=client_id,
            file_bytes=b"\x00" * 32,
        )


@pytest.mark.asyncio
async def test_classifier_failure_raises(monkeypatch: pytest.MonkeyPatch, db_session: Any) -> None:
    session, firm_id, client_id = db_session

    async def boom(_b: bytes) -> ClassificationResult:
        raise RuntimeError("classifier blew up")

    monkeypatch.setattr(pipeline_module, "classify_document", boom)
    with pytest.raises(PipelineError, match="classification"):
        await run_pipeline(
            session=session,
            ca_firm_id=firm_id,
            client_id=client_id,
            file_bytes=_PNG_BYTES,
        )


@pytest.mark.asyncio
async def test_extractor_failure_skips_verification_but_still_persists(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    session, firm_id, client_id = db_session
    _classify_stub(monkeypatch)
    _extract_stub(monkeypatch, raise_exc=RuntimeError("extractor down"))
    result = await run_pipeline(
        session=session,
        ca_firm_id=firm_id,
        client_id=client_id,
        file_bytes=_PNG_BYTES,
    )
    # Persistence still happens even when extraction fails — we want a
    # row pointing at the source file so the CA can manually retry.
    statuses = {s.stage: s.status for s in result.stages}
    assert statuses[PipelineStage.EXTRACTION] is PipelineStageStatus.FAILED
    assert statuses[PipelineStage.VERIFICATION] is PipelineStageStatus.SKIPPED
    assert statuses[PipelineStage.PERSISTENCE] is PipelineStageStatus.OK
    assert result.extraction is None
    assert result.document_id is not None


@pytest.mark.asyncio
async def test_dirty_extraction_records_verification_issues(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    session, firm_id, client_id = db_session
    bad = {
        "vendor": {"name": "V", "gstin": "NOT-A-GSTIN"},
        "buyer": {"name": "B", "gstin": None},
        "line_items": [
            {
                "description": "x",
                "taxable_value": "100.00",
                "cgst_rate": "9",
                "cgst_amount": "100.00",  # wrong math
                "sgst_rate": "9",
                "sgst_amount": "9.00",
            }
        ],
        "totals": {
            "taxable_value": "100.00",
            "total_cgst": "9.00",
            "total_sgst": "9.00",
            "total_igst": "0.00",
            "grand_total": "118.00",
        },
    }
    _classify_stub(monkeypatch)
    _extract_stub(monkeypatch, data=bad)
    _no_firm_rules(monkeypatch)
    result = await run_pipeline(
        session=session,
        ca_firm_id=firm_id,
        client_id=client_id,
        file_bytes=_PNG_BYTES,
    )
    assert result.verification is not None
    codes = {i.code for i in result.verification.issues}
    # GSTIN format check is unchanged — still fires.
    assert "vendor_gstin_invalid" in codes
    # O architectural shift: bad LLM math (CGST=100 on taxable=100) is
    # now SILENTLY FIXED by the Python reconciler before the verifier
    # runs. The verifier sees consistent reconciled values, so
    # cgst_math_mismatch must NOT appear. The hallucinated cell never
    # reaches the CA — they see canonical numbers instead.
    assert "cgst_math_mismatch" not in codes
    # Reconciler did its job: the persisted extraction now carries
    # canonical totals computed from rate * taxable.
    assert result.extraction is not None
    canonical_cgst = result.extraction["totals"]["total_cgst"]
    # 100 * 9% = 9.00 — the correct number, not the hallucinated 100.
    assert canonical_cgst in {"9.00", "9"}
    assert result.overall_status is PipelineStageStatus.OK


# ---------------------------------------------------------------------------
# R1 split — extract_only + finalize_document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_only_returns_extraction_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    """R1: extract_only must run file→classify→extract→verify with no DB access.

    The function takes raw file bytes and returns an ExtractionOutcome
    carrying enough data for the autonomous router to pick a client.
    """
    _classify_stub(monkeypatch, document_type="B2B_INVOICE")
    _extract_stub(monkeypatch)

    outcome = await extract_only(_PNG_BYTES)

    assert isinstance(outcome, ExtractionOutcome)
    assert outcome.document_type == "B2B_INVOICE"
    assert outcome.extraction is not None
    assert "vendor" in outcome.extraction
    # Exactly the four R1 stages should be present (file, classify, extract, verify).
    stage_names = [s.stage for s in outcome.stages]
    assert PipelineStage.FILE_PROCESSING in stage_names
    assert PipelineStage.CLASSIFICATION in stage_names
    assert PipelineStage.EXTRACTION in stage_names
    assert PipelineStage.VERIFICATION in stage_names
    # No tax verdict or persistence stages yet — those belong to finalize_document.
    assert PipelineStage.TAX_VERDICT not in stage_names
    assert PipelineStage.PERSISTENCE not in stage_names


@pytest.mark.asyncio
async def test_finalize_document_persists_and_carries_stages(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Any,
) -> None:
    """R1: finalize_document takes an ExtractionOutcome + client_id and persists."""
    session, firm_id, client_id = db_session
    _classify_stub(monkeypatch)
    _extract_stub(monkeypatch)
    _no_firm_rules(monkeypatch)

    outcome = await extract_only(_PNG_BYTES)
    result = await finalize_document(
        session=session,
        ca_firm_id=firm_id,
        client_id=client_id,
        outcome=outcome,
        source_file_ids=["tg-file-1"],
    )
    assert result.document_id is not None
    assert result.document_type == "B2B_INVOICE"
    # All seven stage entries (4 from extract_only + 3 from finalize) must
    # appear in a single audit log.
    stage_names = {s.stage for s in result.stages}
    for s in PipelineStage:
        assert s in stage_names, f"missing stage {s}"
    statuses = {s.stage: s.status for s in result.stages}
    assert statuses[PipelineStage.PERSISTENCE] is PipelineStageStatus.OK


@pytest.mark.asyncio
async def test_finalize_document_dedupes_same_invoice(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Any,
) -> None:
    """W3-h7 regression: the same (vendor, invoice_number, invoice_date)
    must only persist ONCE — even if the CA re-uploads. Production
    accumulated 3 rows for invoice 127 before the dedup invariant
    landed, polluting the ledger and the filing-approval snapshot.

    Asserts: two finalize calls on the same extraction → identical
    document_id returned + only ONE row in the table.
    """
    from app.db.models.document import Document
    from sqlalchemy import select

    session, firm_id, client_id = db_session
    _classify_stub(monkeypatch)
    _extract_stub(monkeypatch)
    _no_firm_rules(monkeypatch)

    outcome_a = await extract_only(_PNG_BYTES)
    first = await finalize_document(
        session=session,
        ca_firm_id=firm_id,
        client_id=client_id,
        outcome=outcome_a,
        source_file_ids=["tg-file-1"],
    )
    outcome_b = await extract_only(_PNG_BYTES)
    second = await finalize_document(
        session=session,
        ca_firm_id=firm_id,
        client_id=client_id,
        outcome=outcome_b,
        source_file_ids=["tg-file-2"],
    )

    assert first.document_id is not None
    assert second.document_id == first.document_id

    rows = (
        await session.execute(select(Document))
    ).scalars().all()
    assert len(rows) == 1, "dedup must prevent a second row for the same invoice"

    # The duplicate run's persistence stage must be SKIPPED, not OK.
    second_persistence = next(
        s for s in second.stages if s.stage == PipelineStage.PERSISTENCE
    )
    assert second_persistence.status is PipelineStageStatus.SKIPPED
    assert "duplicate" in (second_persistence.error or "")


@pytest.mark.asyncio
async def test_finalize_document_persists_terminal_status_and_filing_period(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Any,
) -> None:
    """W3-h6 regression: after verdict succeeds, the row MUST land with
    ``processing_status='completed'`` and ``filing_period_month/year``
    derived from the invoice date.

    Production prior to this fix saw every processed document sit in
    ``pending`` with NULL filing_period — invisible to exports and the
    filing-approval snapshot. Root cause: ``finalize_document`` called
    ``DocumentQuery.create`` without ``processing_status`` (so the DB
    default ``'pending'`` won) and passed the *input* filing_period
    instead of the ``derived_*`` values computed from the invoice date.
    """
    from app.db.models.document import Document
    from sqlalchemy import select

    session, firm_id, client_id = db_session
    _classify_stub(monkeypatch)
    _extract_stub(monkeypatch)  # extract stub emits invoice_date=2026-04-01
    _no_firm_rules(monkeypatch)

    outcome = await extract_only(_PNG_BYTES)
    result = await finalize_document(
        session=session,
        ca_firm_id=firm_id,
        client_id=client_id,
        outcome=outcome,
        source_file_ids=["tg-file-1"],
    )
    assert result.document_id is not None

    row = (
        await session.execute(
            select(Document).where(Document.id == result.document_id)
        )
    ).scalar_one()
    assert row.processing_status == "completed"
    assert row.filing_period_month == 4
    assert row.filing_period_year == 2026


@pytest.mark.asyncio
async def test_run_pipeline_is_thin_wrapper(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Any,
) -> None:
    """R1: run_pipeline must remain a no-behaviour-change wrapper.

    Existing callers (worker route, tests) must keep working without
    changes. The wrapper composes extract_only + finalize_document.
    """
    session, firm_id, client_id = db_session
    _classify_stub(monkeypatch)
    _extract_stub(monkeypatch)
    _no_firm_rules(monkeypatch)

    result = await run_pipeline(
        session=session,
        ca_firm_id=firm_id,
        client_id=client_id,
        file_bytes=_PNG_BYTES,
        source_file_ids=["tg-file-1"],
    )
    assert result.document_id is not None
    assert result.overall_status is PipelineStageStatus.OK
