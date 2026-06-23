"""Corrected multi-rate invoice math validator — Part 2.2 of the Tax Knowledge Base.

The verifier NEVER compares a single "first found" tax figure against a
computed sum.  It always compares **sum-to-sum** and additionally
cross-checks **slab-to-slab**, which gives a strictly stronger guarantee
than the old single-figure check.

This is the canonical bug fix for the Aditya Enterprises invoice
(173_sales_invoice_Aditya_Enterprises_.pdf) false-positive reconciliation
error — and for every future multi-rate invoice.

Algorithm
---------
1. Group line items by their ``gst_rate`` and compute per-slab CGST/SGST/IGST.
2. Sum computed slabs → computed totals.
3. Sum ``extraction.tax_breakdown`` → stated totals (the actual fix: never
   read a single slab's figure as "the total").
4. Compare SUM to SUM (not first-line to sum).
5. Per-slab cross-check (catches transposed slab figures the aggregate
   check would miss).

Pure Python.  No LLM.  Decimal arithmetic only.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from app.models.extraction_schema import InvoiceExtraction, VerificationResult

__all__ = ["verify_multi_rate_tax"]


TWO_PLACES = Decimal("0.01")
# ₹1 rounding tolerance — matches existing system convention.
TOLERANCE = Decimal("1.00")


def verify_multi_rate_tax(extraction: InvoiceExtraction) -> VerificationResult:
    """Verify that extracted tax figures are internally consistent.

    Returns a :class:`VerificationResult` with ``passed=True`` when no
    arithmetic discrepancies are found, or a list of human-readable
    ``errors`` describing every mismatch.
    """
    errors: list[str] = []

    # ── Step 1: Group line items by their own gst_rate ──────────────────
    computed_by_rate: dict[Decimal, dict[str, Decimal]] = defaultdict(
        lambda: {
            "taxable": Decimal("0"),
            "cgst": Decimal("0"),
            "sgst": Decimal("0"),
            "igst": Decimal("0"),
        }
    )

    vendor_state = (extraction.vendor_gstin or "")[:2]
    buyer_state = (extraction.buyer_gstin or "")[:2]
    is_interstate = vendor_state != buyer_state

    for item in extraction.line_items:
        bucket = computed_by_rate[item.gst_rate]
        bucket["taxable"] += item.taxable_value
        if is_interstate:
            bucket["igst"] += (
                item.taxable_value * item.gst_rate / Decimal("100")
            ).quantize(TWO_PLACES)
        else:
            half_rate = item.gst_rate / Decimal("2")
            bucket["cgst"] += (
                item.taxable_value * half_rate / Decimal("100")
            ).quantize(TWO_PLACES)
            bucket["sgst"] += (
                item.taxable_value * half_rate / Decimal("100")
            ).quantize(TWO_PLACES)

    # ── Step 2: Sum the line-item-derived buckets across ALL rate slabs ─
    computed_total_cgst = sum(b["cgst"] for b in computed_by_rate.values())
    computed_total_sgst = sum(b["sgst"] for b in computed_by_rate.values())
    computed_total_igst = sum(b["igst"] for b in computed_by_rate.values())

    # ── Step 3: Sum the EXTRACTED tax_breakdown across ALL printed slabs ─
    # This is the actual fix: never read a single slab's figure as "the total".
    stated_total_cgst = sum(slab.cgst for slab in extraction.tax_breakdown)
    stated_total_sgst = sum(slab.sgst for slab in extraction.tax_breakdown)
    stated_total_igst = sum(slab.igst for slab in extraction.tax_breakdown)

    # ── Step 4: Compare SUM to SUM, not first-line to sum ───────────────
    if abs(stated_total_cgst - computed_total_cgst) > TOLERANCE:
        errors.append(
            f"CGST mismatch: invoice totals section sums to ₹{stated_total_cgst}, "
            f"line items sum to ₹{computed_total_cgst}."
        )
    if abs(stated_total_sgst - computed_total_sgst) > TOLERANCE:
        errors.append(
            f"SGST mismatch: invoice totals section sums to ₹{stated_total_sgst}, "
            f"line items sum to ₹{computed_total_sgst}."
        )
    if abs(stated_total_igst - computed_total_igst) > TOLERANCE:
        errors.append(
            f"IGST mismatch: invoice totals section sums to ₹{stated_total_igst}, "
            f"line items sum to ₹{computed_total_igst}."
        )

    # ── Step 5: Per-slab cross-check ────────────────────────────────────
    # Catches a case the old system could never catch: two slabs whose
    # totals happen to sum correctly in aggregate but are individually
    # wrong (e.g. a 5% and 18% figure transposed with each other).
    for slab in extraction.tax_breakdown:
        computed = computed_by_rate.get(slab.gst_rate)
        if computed is None:
            errors.append(
                f"Invoice declares a {slab.gst_rate}% slab with no matching line items."
            )
            continue
        if abs(slab.cgst - computed["cgst"]) > TOLERANCE:
            errors.append(
                f"{slab.gst_rate}% slab CGST mismatch: invoice shows ₹{slab.cgst}, "
                f"line items for this slab compute ₹{computed['cgst']}."
            )
        if abs(slab.sgst - computed["sgst"]) > TOLERANCE:
            errors.append(
                f"{slab.gst_rate}% slab SGST mismatch: invoice shows ₹{slab.sgst}, "
                f"line items for this slab compute ₹{computed['sgst']}."
            )

    return VerificationResult(passed=len(errors) == 0, errors=errors)
