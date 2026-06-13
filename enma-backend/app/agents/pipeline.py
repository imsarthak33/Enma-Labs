"""Document extraction pipeline — the linear chain.

The pipeline is intentionally sequential and stateless:

    file bytes
       │
       ▼
    [1] file_processor  (detect kind, optional PDF page split)
       │
       ▼
    [2] classifier      (LLM, layout model) → document_type
       │
       ▼
    [3] extractor       (LLM, vision model) → structured JSON
       │
       ▼
    [4] verifier        (pure Python)      → issues list
       │
       ▼
    PipelineResult  ─►  documents row (JSONB)  ─►  HTML summary

Each stage can fail independently. We capture per-stage status and emit
a single :class:`PipelineResult` that the caller (worker route, test,
admin tool) inspects.

Persistence
-----------
The pipeline writes one row to ``documents`` via
:class:`DocumentQuery`, which inherits :class:`BaseQuery` — so the
``ca_firm_id`` constraint is structurally enforced. Tax verdict
(Phase 5) and a couple of other JSONB fields are left ``None`` here.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.classifier import ClassificationResult, classify_document
from app.agents.context_injector import load_rules_for_engine
from app.agents.extractor import ExtractionResult, extract_document
from app.agents.tax_engine import LockedPeriodError, TaxVerdict, compute_verdict
from app.agents.verifier import VerificationResult, verify_extraction
from app.db.queries.clients import ClientQuery
from app.db.queries.documents import DocumentQuery
from app.db.queries.filings import FilingQuery
from app.logging_setup import get_logger
from app.services.file_processor import (
    FileKind,
    FileProcessingError,
    detect_kind,
    is_image,
    rasterize_pdf_page,
)
from app.tax.reconciler import reconcile_extraction
from app.utils.decimal_utils import quantize_money
from app.tax.itc import PeriodContext
from app.utils.date_utils import FilingPeriod, now_ist, parse_iso_date

_log = get_logger(__name__)

__all__ = [
    "ExtractionOutcome",
    "PipelineResult",
    "PipelineStage",
    "PipelineStageStatus",
    "PipelineStageOutcome",
    "PipelineError",
    "extract_only",
    "finalize_document",
    "run_pipeline",
]


class PipelineStage(StrEnum):
    FILE_PROCESSING = "file_processing"
    CLASSIFICATION = "classification"
    EXTRACTION = "extraction"
    RECONCILIATION = "reconciliation"
    VERIFICATION = "verification"
    TAX_VERDICT = "tax_verdict"
    PERSISTENCE = "persistence"


class PipelineStageStatus(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class PipelineStageOutcome:
    """Per-stage observable result."""

    stage: PipelineStage
    status: PipelineStageStatus
    duration_ms: int
    error: str | None = None


@dataclass(frozen=True)
class ExtractionOutcome:
    """Output of the extract-only phase (R1 split).

    Carries everything routing needs to pick a client *before* persistence
    runs: the classifier's document_type, the extractor's structured JSON
    (vendor + buyer + line items + totals), and the per-stage outcomes
    accumulated so far. The downstream :func:`finalize_document` extends
    these stages and produces the full :class:`PipelineResult`.

    ``extraction`` is ``None`` when the extractor failed — the caller can
    still inspect ``document_type`` (e.g. ``UNKNOWN``) and decide whether
    to prompt or refuse the upload.
    """

    document_type: str
    extraction: dict[str, Any] | None
    verification: VerificationResult | None
    stages: tuple[PipelineStageOutcome, ...]


@dataclass(frozen=True)
class PipelineResult:
    """Aggregate pipeline output."""

    document_id: uuid.UUID | None
    document_type: str
    extraction: dict[str, Any] | None
    verification: VerificationResult | None
    tax_verdict: TaxVerdict | None = None
    stages: tuple[PipelineStageOutcome, ...] = field(default_factory=tuple)

    @property
    def overall_status(self) -> PipelineStageStatus:
        if any(s.status is PipelineStageStatus.FAILED for s in self.stages):
            return PipelineStageStatus.FAILED
        return PipelineStageStatus.OK


class PipelineError(RuntimeError):
    """Catastrophic pipeline failure — no result could be produced."""


# ---------------------------------------------------------------------------
# Stage runner helpers
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


async def extract_only(file_bytes: bytes) -> ExtractionOutcome:
    """Run the client-independent half of the pipeline (R1 split).

    file_processing → classification → extraction → verification.

    This is the "scout pass" the autonomous-routing flow needs: we get
    the buyer's GSTIN/name and the vendor's GSTIN/name from the extractor
    BEFORE we have to decide which client the document belongs to.
    Routing then keys off the extracted fields rather than the upload's
    caption/filename. Tax verdict + persistence are deferred to
    :func:`finalize_document`, which receives the resolved ``client_id``.

    Raises:
        PipelineError: when file processing or classification fail
            irrecoverably. Extraction and verification failures are
            recorded as stage outcomes but do not raise — the caller can
            still inspect ``document_type`` and decide whether to prompt
            the CA or refuse the upload.
    """
    stages: list[PipelineStageOutcome] = []

    # ---- Stage 1: file processing ----------------------------------------
    t0 = _now_ms()
    try:
        image_bytes = _prepare_image_bytes(file_bytes)
    except FileProcessingError as exc:
        stages.append(
            PipelineStageOutcome(
                stage=PipelineStage.FILE_PROCESSING,
                status=PipelineStageStatus.FAILED,
                duration_ms=_now_ms() - t0,
                error=str(exc),
            )
        )
        raise PipelineError(f"file processing failed: {exc}") from exc
    stages.append(
        PipelineStageOutcome(
            stage=PipelineStage.FILE_PROCESSING,
            status=PipelineStageStatus.OK,
            duration_ms=_now_ms() - t0,
        )
    )

    # ---- Stage 2: classification -----------------------------------------
    t0 = _now_ms()
    try:
        classification: ClassificationResult = await classify_document(image_bytes)
    except Exception as exc:
        stages.append(
            PipelineStageOutcome(
                stage=PipelineStage.CLASSIFICATION,
                status=PipelineStageStatus.FAILED,
                duration_ms=_now_ms() - t0,
                error=str(exc),
            )
        )
        raise PipelineError(f"classification failed: {exc}") from exc
    stages.append(
        PipelineStageOutcome(
            stage=PipelineStage.CLASSIFICATION,
            status=PipelineStageStatus.OK,
            duration_ms=_now_ms() - t0,
        )
    )

    # ---- Stage 3: extraction ---------------------------------------------
    t0 = _now_ms()
    extraction_result: ExtractionResult | None = None
    extraction_error: str | None = None
    try:
        extraction_result = await extract_document(
            image_bytes, document_type=classification.document_type
        )
    except Exception as exc:
        extraction_error = str(exc)
    stages.append(
        PipelineStageOutcome(
            stage=PipelineStage.EXTRACTION,
            status=(
                PipelineStageStatus.OK
                if extraction_result is not None
                else PipelineStageStatus.FAILED
            ),
            duration_ms=_now_ms() - t0,
            error=extraction_error,
        )
    )

    extraction_data: dict[str, Any] | None = (
        extraction_result.data if extraction_result is not None else None
    )

    # ---- Stage 3.5: reconciliation (O — Python-driven invoice math) ------
    # The extractor LLM is good at OCR but bad at picking which printed
    # number is the taxable vs the tax. The reconciler computes canonical
    # totals from line_items + observed_totals (NEVER from the LLM's
    # ``totals`` block), then overwrites ``totals`` and ``line_items[].*``
    # amounts with the canonical values so every downstream consumer
    # (verifier, tax engine, summary) reads from a single source of truth.
    t0 = _now_ms()
    if extraction_data is not None:
        try:
            reconciled = reconcile_extraction(extraction_data)
        except Exception as exc:
            stages.append(
                PipelineStageOutcome(
                    stage=PipelineStage.RECONCILIATION,
                    status=PipelineStageStatus.FAILED,
                    duration_ms=_now_ms() - t0,
                    error=str(exc),
                )
            )
            _log.error("pipeline_reconciliation_failed", error=str(exc))
        else:
            # Patch the extraction so downstream code reads canonical numbers.
            extraction_data["totals"] = {
                "taxable_value": str(reconciled.taxable),
                "total_cgst": str(reconciled.cgst_amount),
                "total_sgst": str(reconciled.sgst_amount),
                "total_igst": str(reconciled.igst_amount),
                "grand_total": str(reconciled.grand_total),
            }
            # Per-line math: overwrite the hallucinated *_amount fields
            # with values derived from canonical taxable × rate. Rates the
            # LLM read straight off the column header are preserved.
            for idx, rec_line in enumerate(reconciled.line_items):
                if not (
                    isinstance(extraction_data.get("line_items"), list)
                    and idx < len(extraction_data["line_items"])
                    and isinstance(extraction_data["line_items"][idx], dict)
                ):
                    continue
                target = extraction_data["line_items"][idx]
                target["taxable_value"] = str(rec_line.taxable)
                target["cgst_amount"] = str(
                    quantize_money(rec_line.taxable * reconciled.cgst_rate / Decimal(100))
                )
                target["sgst_amount"] = str(
                    quantize_money(rec_line.taxable * reconciled.sgst_rate / Decimal(100))
                )
                target["igst_amount"] = str(
                    quantize_money(rec_line.taxable * reconciled.igst_rate / Decimal(100))
                )
            extraction_data["reconciliation"] = reconciled.to_dict()
            stages.append(
                PipelineStageOutcome(
                    stage=PipelineStage.RECONCILIATION,
                    status=PipelineStageStatus.OK,
                    duration_ms=_now_ms() - t0,
                )
            )
    else:
        stages.append(
            PipelineStageOutcome(
                stage=PipelineStage.RECONCILIATION,
                status=PipelineStageStatus.SKIPPED,
                duration_ms=_now_ms() - t0,
            )
        )

    # ---- Stage 4: verification (pure Python, no client_id needed) --------
    verification: VerificationResult | None = None
    t0 = _now_ms()
    if extraction_data is not None:
        try:
            verification = verify_extraction(extraction_data)
            stages.append(
                PipelineStageOutcome(
                    stage=PipelineStage.VERIFICATION,
                    status=PipelineStageStatus.OK,
                    duration_ms=_now_ms() - t0,
                )
            )
        except Exception as exc:
            stages.append(
                PipelineStageOutcome(
                    stage=PipelineStage.VERIFICATION,
                    status=PipelineStageStatus.FAILED,
                    duration_ms=_now_ms() - t0,
                    error=str(exc),
                )
            )
    else:
        stages.append(
            PipelineStageOutcome(
                stage=PipelineStage.VERIFICATION,
                status=PipelineStageStatus.SKIPPED,
                duration_ms=_now_ms() - t0,
            )
        )

    return ExtractionOutcome(
        document_type=classification.document_type,
        extraction=extraction_data,
        verification=verification,
        stages=tuple(stages),
    )


async def finalize_document(  # noqa: PLR0912, PLR0915 — linear orchestrator
    *,
    session: AsyncSession,
    ca_firm_id: uuid.UUID,
    client_id: uuid.UUID,
    outcome: ExtractionOutcome,
    filing_period_month: int | None = None,
    filing_period_year: int | None = None,
    source_file_ids: list[str] | None = None,
) -> PipelineResult:
    """Finalise an already-extracted document against a resolved client.

    Runs the tax engine, enforces the locked-period invariant, and
    persists a ``documents`` row. Carries forward the per-stage timings
    accumulated by :func:`extract_only` so a single PipelineResult still
    audits the entire pipeline.

    Raises:
        PipelineError: only on persistence failure. Tax-verdict failures
            land as observable stage outcomes (the document still saves).
    """
    stages: list[PipelineStageOutcome] = list(outcome.stages)
    classification_type = outcome.document_type
    extraction_data = outcome.extraction
    verification = outcome.verification

    # ---- Stage 5: tax verdict --------------------------------------------
    verdict: TaxVerdict | None = None
    t0 = _now_ms()
    if extraction_data is not None:
        try:
            verdict = await _run_tax_engine(
                session=session,
                ca_firm_id=ca_firm_id,
                client_id=client_id,
                extraction=extraction_data,
                document_type=classification_type,
                filing_period_month=filing_period_month,
                filing_period_year=filing_period_year,
            )
            stages.append(
                PipelineStageOutcome(
                    stage=PipelineStage.TAX_VERDICT,
                    status=PipelineStageStatus.OK,
                    duration_ms=_now_ms() - t0,
                )
            )
        except LockedPeriodError as exc:
            # Frozen-verdict invariant — the document targets a period
            # the firm has already approved. Skip without failing the
            # pipeline; the CA can investigate via audit.
            _log.info("pipeline_skipped_locked_period", reason=str(exc))
            stages.append(
                PipelineStageOutcome(
                    stage=PipelineStage.TAX_VERDICT,
                    status=PipelineStageStatus.SKIPPED,
                    duration_ms=_now_ms() - t0,
                    error=str(exc),
                )
            )
        except Exception as exc:  # — engine errors are observable but not fatal
            stages.append(
                PipelineStageOutcome(
                    stage=PipelineStage.TAX_VERDICT,
                    status=PipelineStageStatus.FAILED,
                    duration_ms=_now_ms() - t0,
                    error=str(exc),
                )
            )
            _log.error("pipeline_tax_verdict_failed", error=str(exc))
    else:
        stages.append(
            PipelineStageOutcome(
                stage=PipelineStage.TAX_VERDICT,
                status=PipelineStageStatus.SKIPPED,
                duration_ms=_now_ms() - t0,
            )
        )

    # ---- Filing-period lock check ----------------------------------------
    # Phase 6 invariant: a (firm, month, year) approved via ENMA APPROVE
    # FILING is frozen — new documents whose invoice date falls inside a
    # locked period are refused (decision in Phase 6 plan, ADR-005 rev 2).
    invoice_date_iso = (extraction_data or {}).get("invoice_date")
    invoice_date_parsed = parse_iso_date(invoice_date_iso)
    derived_month = filing_period_month
    derived_year = filing_period_year
    if invoice_date_parsed is not None and derived_month is None:
        derived_month = invoice_date_parsed.month
        derived_year = invoice_date_parsed.year
    if derived_month is not None and derived_year is not None:
        locked = await FilingQuery(
            session=session, ca_firm_id=ca_firm_id
        ).is_period_locked(month=derived_month, year=derived_year)
        if locked:
            _log.info(
                "pipeline_refused_locked_period",
                month=derived_month,
                year=derived_year,
            )
            stages.append(
                PipelineStageOutcome(
                    stage=PipelineStage.PERSISTENCE,
                    status=PipelineStageStatus.SKIPPED,
                    duration_ms=0,
                    error=f"period {derived_month}/{derived_year} is locked",
                )
            )
            return PipelineResult(
                document_id=None,
                document_type=classification_type,
                extraction=extraction_data,
                verification=verification,
                tax_verdict=verdict,
                stages=tuple(stages),
            )

    # ---- Stage 6: persistence --------------------------------------------
    t0 = _now_ms()
    document_id: uuid.UUID | None = None
    queries = DocumentQuery(session=session, ca_firm_id=ca_firm_id)
    try:
        doc = await queries.create(
            client_id=client_id,
            document_type=classification_type,
            source_file_ids={"file_ids": source_file_ids or []},
            extraction_data=extraction_data or {},
            tax_verdict=verdict.to_jsonb() if verdict is not None else None,
            verification_result=(verification.to_dict() if verification is not None else None),
            filing_period_month=filing_period_month,
            filing_period_year=filing_period_year,
        )
        await session.commit()
        document_id = doc.id
        stages.append(
            PipelineStageOutcome(
                stage=PipelineStage.PERSISTENCE,
                status=PipelineStageStatus.OK,
                duration_ms=_now_ms() - t0,
            )
        )
    except Exception as exc:
        await session.rollback()
        stages.append(
            PipelineStageOutcome(
                stage=PipelineStage.PERSISTENCE,
                status=PipelineStageStatus.FAILED,
                duration_ms=_now_ms() - t0,
                error=str(exc),
            )
        )
        # Persistence failures are loud — the CA still gets a Telegram
        # summary based on the in-memory result, but we log+raise for
        # observability.
        _log.error("pipeline_persistence_failed", error=str(exc))

    return PipelineResult(
        document_id=document_id,
        document_type=classification_type,
        extraction=extraction_data,
        verification=verification,
        tax_verdict=verdict,
        stages=tuple(stages),
    )


async def run_pipeline(
    *,
    session: AsyncSession,
    ca_firm_id: uuid.UUID,
    client_id: uuid.UUID,
    file_bytes: bytes,
    filing_period_month: int | None = None,
    filing_period_year: int | None = None,
    source_file_ids: list[str] | None = None,
) -> PipelineResult:
    """Thin backward-compat wrapper that runs the full pipeline end-to-end.

    Preserved for callers that already have a resolved ``client_id`` and
    want a one-shot extract-and-persist call. New code targeting the
    autonomous-routing flow should call :func:`extract_only` first
    (to obtain the routing keys), then :func:`finalize_document` once a
    client has been picked.

    Raises:
        PipelineError: bubbled from :func:`extract_only` (file processing
            or classification) and from :func:`finalize_document`
            (persistence failure).
    """
    outcome = await extract_only(file_bytes)
    return await finalize_document(
        session=session,
        ca_firm_id=ca_firm_id,
        client_id=client_id,
        outcome=outcome,
        filing_period_month=filing_period_month,
        filing_period_year=filing_period_year,
        source_file_ids=source_file_ids,
    )


# ---------------------------------------------------------------------------
# Tax engine integration
# ---------------------------------------------------------------------------


async def _run_tax_engine(
    *,
    session: AsyncSession,
    ca_firm_id: uuid.UUID,
    client_id: uuid.UUID,
    extraction: dict[str, Any],
    document_type: str,
    filing_period_month: int | None,
    filing_period_year: int | None,
) -> TaxVerdict:
    """Assemble the engine inputs and run :func:`compute_verdict`.

    DB I/O happens here, NOT inside the engine itself — keeping the
    engine pure makes it trivially unit-testable. We:

      * Look up the client's ``gst_tds_deductor`` flag.
      * Build the locked-period set from ``filing_approvals``.
      * Build the current ``FilingPeriod`` from the supplied period or
        IST today.
      * Retrieve and precedence-order the firm rules via
        :func:`load_rules_for_engine`.
      * Determine the invoice's own period so the frozen-verdict guard
        can fire when applicable.
    """
    client_query = ClientQuery(session=session, ca_firm_id=ca_firm_id)
    client = await client_query.get_by_id(client_id)
    gst_tds_deductor = bool(client.gst_tds_deductor) if client is not None else False

    filing_query = FilingQuery(session=session, ca_firm_id=ca_firm_id)
    locked_periods = await filing_query.list_locked_periods()

    today_ist = now_ist().date()
    if filing_period_month is not None and filing_period_year is not None:
        current_period = FilingPeriod(year=filing_period_year, month=filing_period_month)
    else:
        current_period = FilingPeriod.from_date(today_ist)

    period_context = PeriodContext(
        current=current_period,
        locked_periods=locked_periods,
        as_of=today_ist,
    )

    invoice_period: FilingPeriod | None = None
    invoice_date = parse_iso_date(extraction.get("invoice_date"))
    if invoice_date is not None:
        invoice_period = FilingPeriod.from_date(invoice_date)

    vendor_name = (
        extraction.get("vendor", {}).get("name")
        if isinstance(extraction.get("vendor"), dict)
        else None
    )
    query_text = f"{document_type} {vendor_name or ''}".strip()
    firm_rules = await load_rules_for_engine(
        session=session,
        ca_firm_id=ca_firm_id,
        client_id=client_id,
        query_text=query_text,
    )

    return compute_verdict(
        extraction,
        document_type=document_type,
        period_context=period_context,
        gst_tds_deductor=gst_tds_deductor,
        firm_rules=firm_rules,
        invoice_period=invoice_period,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _prepare_image_bytes(file_bytes: bytes) -> bytes:
    """Return image bytes suitable for handing to the vision LLM.

    For an image input: pass through as-is.
    For a PDF input: rasterise the first page to PNG via PyMuPDF.

    We rasterise rather than pass PDF bytes through because NVIDIA NIM's
    OpenAI-compatible schema rejects the ``file`` content part — only
    ``text`` and ``image_url`` are accepted. Rasterising upstream of the
    LLM keeps every vision model we use (NIM nemotron-ocr, Llama vision,
    gpt-4o) on the same single code path. Phase 4 still handles one
    page per ``run_pipeline`` call; multi-page handling is a Phase 4.5
    extension if needed.

    Anything else raises :class:`FileProcessingError`.
    """
    kind = detect_kind(file_bytes)
    if is_image(kind):
        return file_bytes
    if kind is FileKind.PDF:
        return rasterize_pdf_page(file_bytes, page_index=0)
    raise FileProcessingError(f"unsupported file kind: {kind.value}")
