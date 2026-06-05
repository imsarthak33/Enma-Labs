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
    PipelineError,
    PipelineStage,
    PipelineStageStatus,
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
                "trade_name TEXT, gstin TEXT, is_active INTEGER DEFAULT 1, "
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
                "processing_time_ms INTEGER, created_at TEXT, updated_at TEXT)"
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_image(monkeypatch: pytest.MonkeyPatch, db_session: Any) -> None:
    session, firm_id, client_id = db_session
    _classify_stub(monkeypatch)
    _extract_stub(monkeypatch)
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
async def test_pdf_first_page_extracted(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    session, firm_id, client_id = db_session
    _classify_stub(monkeypatch)
    _extract_stub(monkeypatch)
    result = await run_pipeline(
        session=session,
        ca_firm_id=firm_id,
        client_id=client_id,
        file_bytes=_make_pdf(3),
    )
    assert result.overall_status is PipelineStageStatus.OK


@pytest.mark.asyncio
async def test_unsupported_file_kind_raises(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    session, firm_id, client_id = db_session
    _classify_stub(monkeypatch)
    _extract_stub(monkeypatch)
    with pytest.raises(PipelineError, match="file processing"):
        await run_pipeline(
            session=session,
            ca_firm_id=firm_id,
            client_id=client_id,
            file_bytes=b"\x00" * 32,
        )


@pytest.mark.asyncio
async def test_classifier_failure_raises(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
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
    result = await run_pipeline(
        session=session,
        ca_firm_id=firm_id,
        client_id=client_id,
        file_bytes=_PNG_BYTES,
    )
    assert result.verification is not None
    codes = {i.code for i in result.verification.issues}
    assert "vendor_gstin_invalid" in codes
    assert "cgst_math_mismatch" in codes
    # Pipeline overall is OK (no STAGE failed); verifier flagged issues.
    assert result.overall_status is PipelineStageStatus.OK
