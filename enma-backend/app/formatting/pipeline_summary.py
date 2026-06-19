"""Compose HTML Telegram summaries from a :class:`PipelineResult`.

This is the only place pipeline output is turned into user-facing text.
Two reasons that matters:

  * **HTML safety.** Every interpolated value goes through ``safe_text``
    so a vendor name with ``<script>`` in it can't break the message.
  * **Voice consistency.** Tone, ordering, and what we choose to surface
    vs. omit lives here. Editing one function tunes the whole UX.

The summary intentionally LEADS with the verification outcome (the
"what does the CA need to act on?" question) and follows with the
extracted highlights.
"""

from __future__ import annotations

from typing import Any, Final

from app.agents.pipeline import PipelineResult, PipelineStage, PipelineStageStatus
from app.agents.verifier import (
    VerificationIssue,
    VerificationResult,
    VerificationSeverity,
)
from app.formatting.telegram_html import bold, code, italic, safe_text

__all__ = ["render_pipeline_summary"]


_DUP_PREFIX: Final[str] = "duplicate of "
_LOCKED_MARKER: Final[str] = "is locked"


# ---------------------------------------------------------------------------
# Top-level entrypoint
# ---------------------------------------------------------------------------


def render_pipeline_summary(result: PipelineResult) -> str:
    """Compose the Telegram HTML summary for a pipeline result."""
    sections: list[str] = []
    sections.append(_header(result))
    # W3.5-a: surface dedup hits prominently so a CA re-uploading the
    # same invoice sees we recognised it (vs thinking we re-processed).
    dup_banner = _duplicate_banner(result)
    if dup_banner:
        sections.append(dup_banner)
    # W3.5-e: locked-period refusal — make explicit why the doc was
    # NOT added to the books. Without this the CA sees "Ref: not
    # persisted" with no explanation and thinks the pipeline silently
    # broke.
    locked_banner = _locked_period_banner(result)
    if locked_banner:
        sections.append(locked_banner)
    sections.append(_verdict_line(result.verification))
    extraction = result.extraction or {}
    sections.append(_extraction_block(extraction))
    issues_block = _issues_block(result.verification)
    if issues_block:
        sections.append(issues_block)
    sections.append(_stage_block(result))
    return "\n\n".join(s for s in sections if s)


def _duplicate_ref(result: PipelineResult) -> str | None:
    """Return the 8-char ref of the existing doc when this run was a dedup hit.

    Detection: ``finalize_document`` returns a PipelineResult whose
    PERSISTENCE stage is SKIPPED with an error string starting
    ``"duplicate of <uuid>"``. Returns the first 8 hex chars of that
    UUID, matching the ``Ref:`` style shown in :func:`_header`.
    """
    for stage in result.stages:
        if (
            stage.stage is PipelineStage.PERSISTENCE
            and stage.status is PipelineStageStatus.SKIPPED
            and stage.error
            and stage.error.startswith(_DUP_PREFIX)
        ):
            existing_id = stage.error[len(_DUP_PREFIX):].strip()
            return existing_id[:8] if existing_id else None
    return None


def _duplicate_banner(result: PipelineResult) -> str:
    """Render the 'this is a duplicate of <ref>' banner, or empty if not."""
    dup_ref = _duplicate_ref(result)
    if dup_ref is None:
        return ""
    return (
        f"{italic('Re-upload detected.')} "
        f"Already in your ledger as {code(dup_ref)} — nothing new persisted."
    )


def _locked_period_banner(result: PipelineResult) -> str:
    """Render an explicit refusal when persistence skipped on a locked period.

    finalize_document emits the SKIPPED persistence stage with an
    error like ``"period 8/2025 is locked"`` when the invoice's
    derived period falls inside a filing the CA already approved.
    Without surfacing this the summary just says "Ref: not persisted"
    and the CA assumes a silent failure.
    """
    for stage in result.stages:
        if (
            stage.stage is PipelineStage.PERSISTENCE
            and stage.status is PipelineStageStatus.SKIPPED
            and stage.error
            and _LOCKED_MARKER in stage.error
        ):
            return (
                f"⚠ {bold('Not added to books.')} "
                f"{safe_text(stage.error.capitalize())}. "
                f"The filing was approved earlier — adding this invoice "
                f"would change the locked totals. If it really belongs "
                f"in this period, unlock the filing first, or re-date "
                f"the invoice."
            )
    return ""


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _header(result: PipelineResult) -> str:
    doc_id_str = (
        code(str(result.document_id)[:8]) if result.document_id else italic("not persisted")
    )
    return (
        f"{bold('Document processed')}\n"
        f"Type: {bold(safe_text(result.document_type))}\n"
        f"Ref: {doc_id_str}"
    )


def _verdict_line(verification: VerificationResult | None) -> str:
    if verification is None:
        return italic("Verification skipped (extraction failed).")
    errors = [i for i in verification.issues if i.severity is VerificationSeverity.ERROR]
    warnings = [i for i in verification.issues if i.severity is VerificationSeverity.WARNING]
    if errors:
        return (
            f"{bold('Verdict:')} {bold('NEEDS REVIEW')} "
            f"({len(errors)} error{'s' if len(errors) != 1 else ''}, "
            f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''})"
        )
    if warnings:
        return f"{bold('Verdict:')} CLEAN with {len(warnings)} warning(s)"
    return f"{bold('Verdict:')} CLEAN"


def _extraction_block(extraction: dict[str, Any]) -> str:
    if not extraction:
        return ""
    vendor = extraction.get("vendor") or {}
    buyer = extraction.get("buyer") or {}
    totals = extraction.get("totals") or {}
    line_items = extraction.get("line_items") or []

    lines: list[str] = [bold("Extraction")]
    if isinstance(vendor, dict):
        lines.append(
            f"Vendor: {safe_text(vendor.get('name') or '—')} "
            f"({code(safe_text(vendor.get('gstin') or '—'))})"
        )
    if isinstance(buyer, dict):
        lines.append(
            f"Buyer: {safe_text(buyer.get('name') or '—')} "
            f"({code(safe_text(buyer.get('gstin') or '—'))})"
        )
    if extraction.get("invoice_number"):
        lines.append(f"Invoice #: {code(safe_text(extraction['invoice_number']))}")
    if extraction.get("invoice_date"):
        lines.append(f"Date: {safe_text(extraction['invoice_date'])}")
    if isinstance(totals, dict) and totals.get("grand_total") is not None:
        lines.append(
            f"Grand total: {code(safe_text(totals.get('grand_total')))} "
            f"(taxable {safe_text(totals.get('taxable_value') or '—')})"
        )
        # O — surface the CGST/SGST/IGST breakdown (canonical, never LLM math).
        for tax_label, key in (
            ("CGST", "total_cgst"),
            ("SGST", "total_sgst"),
            ("IGST", "total_igst"),
        ):
            amount = totals.get(key)
            if amount not in (None, "", "0", "0.00"):
                lines.append(f"{tax_label}: {code(safe_text(amount))}")
    lines.append(f"Line items: {bold(len(line_items) if isinstance(line_items, list) else 0)}")
    # O — surface reconciler discrepancies so the CA sees what disagreed
    # between the printed invoice and the canonical math.
    reconciliation = extraction.get("reconciliation") or {}
    if isinstance(reconciliation, dict):
        discrepancies = reconciliation.get("discrepancies") or []
        if isinstance(discrepancies, list) and discrepancies:
            lines.append("")
            lines.append(bold("Reconciliation flags"))
            for d in discrepancies[:5]:
                if isinstance(d, dict) and d.get("message"):
                    lines.append(f"• {italic(safe_text(d['message']))}")
    return "\n".join(lines)


def _issues_block(verification: VerificationResult | None) -> str:
    if verification is None or not verification.issues:
        return ""
    # Order: ERROR first, then WARNING, then INFO. Cap at 10 to keep
    # Telegram messages readable.
    ordered = sorted(verification.issues, key=_severity_sort_key)[:10]
    lines: list[str] = [bold("Verification issues")]
    for issue in ordered:
        lines.append(_format_issue(issue))
    if len(verification.issues) > 10:
        lines.append(italic(f"… and {len(verification.issues) - 10} more"))
    return "\n".join(lines)


def _stage_block(result: PipelineResult) -> str:
    """Compact per-stage timing for engineering visibility."""
    pieces = []
    for s in result.stages:
        glyph = _stage_glyph(s.status)
        pieces.append(f"{glyph} {safe_text(s.stage.value)} {italic(f'{s.duration_ms} ms')}")
    return bold("Pipeline") + "\n" + "\n".join(pieces)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SEVERITY_RANK = {
    VerificationSeverity.ERROR: 0,
    VerificationSeverity.WARNING: 1,
    VerificationSeverity.INFO: 2,
}


def _severity_sort_key(issue: VerificationIssue) -> int:
    return _SEVERITY_RANK[issue.severity]


def _format_issue(issue: VerificationIssue) -> str:
    sev_label = bold(safe_text(issue.severity.value))
    code_label = code(safe_text(issue.code))
    field_label = f" @ {code(safe_text(issue.field.path))}" if issue.field else ""
    return f"• {sev_label} {code_label}{field_label}: {safe_text(issue.message)}"


def _stage_glyph(status: PipelineStageStatus) -> str:
    if status is PipelineStageStatus.OK:
        return "[ok]"
    if status is PipelineStageStatus.SKIPPED:
        return "[skip]"
    return "[fail]"
