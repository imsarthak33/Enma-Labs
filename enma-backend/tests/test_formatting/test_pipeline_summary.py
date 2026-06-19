"""Tests for the pipeline → HTML summary renderer."""

from __future__ import annotations

import uuid

from app.agents.pipeline import (
    PipelineResult,
    PipelineStage,
    PipelineStageOutcome,
    PipelineStageStatus,
)
from app.agents.verifier import (
    FieldRef,
    VerificationIssue,
    VerificationResult,
    VerificationSeverity,
)
from app.formatting.pipeline_summary import render_pipeline_summary


def _stages_ok() -> tuple[PipelineStageOutcome, ...]:
    return tuple(
        PipelineStageOutcome(stage=s, status=PipelineStageStatus.OK, duration_ms=1)
        for s in PipelineStage
    )


_UNSET = object()


def _result(
    *,
    extraction: dict | None = None,
    verification: VerificationResult | None = None,
    doc_id: uuid.UUID | None | object = _UNSET,
    document_type: str = "B2B_INVOICE",
) -> PipelineResult:
    return PipelineResult(
        document_id=uuid.uuid4() if doc_id is _UNSET else doc_id,  # type: ignore[arg-type]
        document_type=document_type,
        extraction=extraction,
        verification=verification,
        stages=_stages_ok(),
    )


class TestRenderSummary:
    def test_clean_invoice_summary(self) -> None:
        verification = VerificationResult(issues=())
        extraction = {
            "vendor": {"name": "Acme & Sons", "gstin": "29AAAGU0010P1Z5"},
            "buyer": {"name": "Buyer Ltd", "gstin": "27AABCU9603R1ZN"},
            "invoice_number": "INV-1",
            "invoice_date": "2026-04-01",
            "totals": {"taxable_value": "1000.00", "grand_total": "1180.00"},
            "line_items": [{}, {}],
        }
        html = render_pipeline_summary(_result(extraction=extraction, verification=verification))
        # Header + verdict + extraction + stages — but no issues block.
        assert "Document processed" in html
        assert "CLEAN" in html
        assert "Acme &amp; Sons" in html  # HTML-escaped
        assert "INV-1" in html
        # The PNG-style "<i>" wrapper from italic helper is present somewhere.
        assert "<b>" in html
        assert "Verification issues" not in html  # no issues block

    def test_error_verdict_marked_needs_review(self) -> None:
        verification = VerificationResult(
            issues=(
                VerificationIssue(
                    severity=VerificationSeverity.ERROR,
                    code="vendor_gstin_invalid",
                    message="bad gstin",
                    field=FieldRef("vendor.gstin"),
                ),
            )
        )
        html = render_pipeline_summary(_result(extraction={}, verification=verification))
        assert "NEEDS REVIEW" in html
        assert "vendor_gstin_invalid" in html

    def test_warnings_only_marked_clean_with_warnings(self) -> None:
        verification = VerificationResult(
            issues=(
                VerificationIssue(
                    severity=VerificationSeverity.WARNING,
                    code="vendor_gstin_missing",
                    message="no gstin",
                ),
            )
        )
        html = render_pipeline_summary(_result(extraction={}, verification=verification))
        assert "CLEAN with 1 warning" in html

    def test_skipped_verification(self) -> None:
        html = render_pipeline_summary(_result(extraction=None, verification=None))
        assert "Verification skipped" in html

    def test_issue_block_truncated_at_ten(self) -> None:
        many = tuple(
            VerificationIssue(
                severity=VerificationSeverity.ERROR,
                code=f"err_{i}",
                message=f"error {i}",
            )
            for i in range(15)
        )
        verification = VerificationResult(issues=many)
        html = render_pipeline_summary(_result(extraction={}, verification=verification))
        # Only 10 should be rendered explicitly; the rest summarised.
        for i in range(10):
            assert f"err_{i}" in html
        assert "and 5 more" in html

    def test_xss_attempt_in_vendor_name_is_escaped(self) -> None:
        extraction = {
            "vendor": {"name": "<script>alert(1)</script>", "gstin": "X"},
            "buyer": {"name": None, "gstin": None},
            "line_items": [],
            "totals": {},
        }
        html = render_pipeline_summary(
            _result(extraction=extraction, verification=VerificationResult(issues=()))
        )
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_unpersisted_result_shows_not_persisted(self) -> None:
        html = render_pipeline_summary(
            _result(
                extraction={},
                verification=VerificationResult(issues=()),
                doc_id=None,
            )
        )
        assert "not persisted" in html


class TestDuplicateBanner:
    """W3.5-a regression: dedup hits must be visible in the summary."""

    def _dedup_stages(self, existing_id: str) -> tuple[PipelineStageOutcome, ...]:
        # finalize_document emits SKIPPED persistence with "duplicate of <uuid>"
        # error string on the dedup return path.
        stages = []
        for s in PipelineStage:
            if s is PipelineStage.PERSISTENCE:
                stages.append(
                    PipelineStageOutcome(
                        stage=s,
                        status=PipelineStageStatus.SKIPPED,
                        duration_ms=0,
                        error=f"duplicate of {existing_id}",
                    )
                )
            else:
                stages.append(
                    PipelineStageOutcome(stage=s, status=PipelineStageStatus.OK, duration_ms=1)
                )
        return tuple(stages)

    def test_duplicate_banner_renders(self) -> None:
        existing = uuid.uuid4()
        result = PipelineResult(
            document_id=existing,
            document_type="B2B_INVOICE",
            extraction={"vendor": {"name": "V"}, "totals": {}, "line_items": []},
            verification=VerificationResult(issues=()),
            stages=self._dedup_stages(str(existing)),
        )
        html = render_pipeline_summary(result)
        assert "Re-upload detected" in html
        assert str(existing)[:8] in html

    def test_no_banner_when_persistence_ok(self) -> None:
        result = PipelineResult(
            document_id=uuid.uuid4(),
            document_type="B2B_INVOICE",
            extraction={"vendor": {"name": "V"}, "totals": {}, "line_items": []},
            verification=VerificationResult(issues=()),
            stages=_stages_ok(),
        )
        html = render_pipeline_summary(result)
        assert "Re-upload detected" not in html


class TestLockedPeriodBanner:
    """W3.5-e regression: when finalize_document refuses to insert into
    a locked filing period, the summary must say so explicitly. Without
    this the CA sees 'Ref: not persisted' and thinks the pipeline
    silently broke.
    """

    def _locked_stages(self) -> tuple[PipelineStageOutcome, ...]:
        stages = []
        for s in PipelineStage:
            if s is PipelineStage.PERSISTENCE:
                stages.append(
                    PipelineStageOutcome(
                        stage=s,
                        status=PipelineStageStatus.SKIPPED,
                        duration_ms=0,
                        error="period 8/2025 is locked",
                    )
                )
            else:
                stages.append(
                    PipelineStageOutcome(stage=s, status=PipelineStageStatus.OK, duration_ms=1)
                )
        return tuple(stages)

    def test_locked_banner_renders(self) -> None:
        result = PipelineResult(
            document_id=None,  # — refused, no row created
            document_type="B2B_INVOICE",
            extraction={"vendor": {"name": "V"}, "totals": {}, "line_items": []},
            verification=VerificationResult(issues=()),
            stages=self._locked_stages(),
        )
        html = render_pipeline_summary(result)
        assert "Not added to books" in html
        assert "8/2025" in html
        assert "locked" in html.lower()

    def test_no_locked_banner_when_persistence_ok(self) -> None:
        result = PipelineResult(
            document_id=uuid.uuid4(),
            document_type="B2B_INVOICE",
            extraction={"vendor": {"name": "V"}, "totals": {}, "line_items": []},
            verification=VerificationResult(issues=()),
            stages=_stages_ok(),
        )
        html = render_pipeline_summary(result)
        assert "Not added to books" not in html
