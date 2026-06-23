"""Multi-rate invoice extraction schema — Part 2.1 of the Tax Knowledge Base.

Every extracted invoice carries a ``tax_breakdown`` array (one entry per
GST rate slab present on the invoice), **never** a single flat
``cgst``/``sgst``/``igst`` field.  The flat totals (``cgst_total``,
``sgst_total``, ``igst_total``) remain as derived convenience values
that MUST equal the sum of the corresponding fields across all
``tax_breakdown`` entries.

This schema replaces the old single-rate assumption that caused
false-positive reconciliation errors on invoices mixing (e.g.) 5% and
18% line items — which, after the GST 2.0 reform of 22 September 2025,
is now extremely common in sectors like textiles.

Nothing in this module touches the database, HTTP, or any LLM client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, model_validator

__all__ = [
    "InvoiceExtraction",
    "LineItem",
    "TaxSlabEntry",
    "VerificationResult",
]


# ---------------------------------------------------------------------------
# Pydantic models — the extraction boundary
# ---------------------------------------------------------------------------


class TaxSlabEntry(BaseModel):
    """One GST rate bucket as printed in the invoice totals section,
    or as aggregated from line items sharing the same rate."""

    gst_rate: Decimal           # e.g. Decimal("5.00") or Decimal("18.00")
    taxable_value: Decimal
    cgst: Decimal = Decimal("0")
    sgst: Decimal = Decimal("0")
    igst: Decimal = Decimal("0")


class LineItem(BaseModel):
    """A single line on the invoice.

    ``gst_rate`` is MANDATORY — every line item must carry its own rate
    so the verifier can group by slab and cross-check per-slab totals.
    """

    description: str
    hsn_code: str | None = None
    quantity: Decimal
    unit: str | None = None
    unit_rate: Decimal
    gst_rate: Decimal           # MANDATORY — per-line GST rate
    taxable_value: Decimal
    line_total: Decimal


class InvoiceExtraction(BaseModel):
    """Complete extraction output for one invoice.

    ``tax_breakdown`` has one entry PER RATE SLAB present — never
    collapsed into a single bucket.  ``cgst_total`` / ``sgst_total`` /
    ``igst_total`` MUST equal the sum of the corresponding fields
    across all ``tax_breakdown`` entries.
    """

    vendor_name: str
    vendor_gstin: str
    buyer_name: str
    buyer_gstin: str | None = None
    invoice_number: str
    invoice_date: str
    line_items: list[LineItem]
    tax_breakdown: list[TaxSlabEntry]
    taxable_value_total: Decimal
    cgst_total: Decimal
    sgst_total: Decimal
    igst_total: Decimal
    grand_total: Decimal

    @model_validator(mode="after")
    def _totals_match_breakdown(self) -> "InvoiceExtraction":
        """Sanity-check that the flat totals equal the breakdown sums."""
        if self.tax_breakdown:
            expected_cgst = sum(s.cgst for s in self.tax_breakdown)
            expected_sgst = sum(s.sgst for s in self.tax_breakdown)
            expected_igst = sum(s.igst for s in self.tax_breakdown)
            # Allow ₹1 tolerance for rounding across slabs.
            tolerance = Decimal("1.00")
            if abs(self.cgst_total - expected_cgst) > tolerance:
                raise ValueError(
                    f"cgst_total ({self.cgst_total}) does not match "
                    f"sum of tax_breakdown CGST ({expected_cgst})"
                )
            if abs(self.sgst_total - expected_sgst) > tolerance:
                raise ValueError(
                    f"sgst_total ({self.sgst_total}) does not match "
                    f"sum of tax_breakdown SGST ({expected_sgst})"
                )
            if abs(self.igst_total - expected_igst) > tolerance:
                raise ValueError(
                    f"igst_total ({self.igst_total}) does not match "
                    f"sum of tax_breakdown IGST ({expected_igst})"
                )
        return self


# ---------------------------------------------------------------------------
# Verification result — output of the verifier
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of the multi-rate tax verification.

    ``passed`` is True when no arithmetic discrepancies were found.
    ``errors`` contains human-readable descriptions of every mismatch.
    """

    passed: bool
    errors: list[str] = field(default_factory=list)
