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
from enum import StrEnum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.classifier import ClassificationResult, classify_document
from app.agents.extractor import ExtractionResult, extract_document
from app.agents.verifier import VerificationResult, verify_extraction
from app.db.queries.documents import DocumentQuery
from app.logging_setup import get_logger
from app.services.file_processor import (
    FileKind,
    FileProcessingError,
    detect_kind,
    is_image,
    split_pdf_pages,
)

_log = get_logger(__name__)

__all__ = [
    "PipelineResult",
    "PipelineStage",
    "PipelineStageStatus",
    "PipelineStageOutcome",
    "PipelineError",
    "run_pipeline",
]


class PipelineStage(StrEnum):
    FILE_PROCESSING = "file_processing"
    CLASSIFICATION = "classification"
    EXTRACTION = "extraction"
    VERIFICATION = "verification"
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
class PipelineResult:
    """Aggregate pipeline output."""

    document_id: uuid.UUID | None
    document_type: str
    extraction: dict[str, Any] | None
    verification: VerificationResult | None
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
    """Run the full extraction pipeline for a single document.

    The function is a single async sequence — no fan-out. Pipeline-level
    parallelism (e.g. processing all pages of a multi-page PDF) is
    handled by the caller; one ``run_pipeline`` invocation = one
    document row.

    Raises:
        PipelineError: when a stage that has no useful fallback fails
            (unsupported file kind, classifier error before we even know
            the type, persistence failure).
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
        # We have no document_type — pipeline cannot proceed.
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

    # ---- Stage 4: verification -------------------------------------------
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

    # ---- Stage 5: persistence --------------------------------------------
    t0 = _now_ms()
    document_id: uuid.UUID | None = None
    queries = DocumentQuery(session=session, ca_firm_id=ca_firm_id)
    try:
        doc = await queries.create(
            client_id=client_id,
            document_type=classification.document_type,
            source_file_ids={"file_ids": source_file_ids or []},
            extraction_data=extraction_data or {},
            tax_verdict=None,  # Phase 5
            verification_result=(
                verification.to_dict() if verification is not None else None
            ),
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
        document_type=classification.document_type,
        extraction=extraction_data,
        verification=verification,
        stages=tuple(stages),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _prepare_image_bytes(file_bytes: bytes) -> bytes:
    """Return image bytes suitable for handing to the vision LLM.

    For an image input: pass through as-is.
    For a PDF input: split and return the FIRST page only (Phase 4
    handles one page at a time; multi-page processing is a Phase 4.5
    extension if needed).

    Anything else raises :class:`FileProcessingError`.
    """
    kind = detect_kind(file_bytes)
    if is_image(kind):
        return file_bytes
    if kind is FileKind.PDF:
        pages = split_pdf_pages(file_bytes)
        if not pages:
            raise FileProcessingError("PDF split returned zero pages")
        return pages[0]
    raise FileProcessingError(f"unsupported file kind: {kind.value}")
