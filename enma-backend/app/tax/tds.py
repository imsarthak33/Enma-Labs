"""Section 51 GST-TDS — the only TDS encoded in v1.

GST-TDS applies when:

  * The recipient (the buyer / our firm's *client*) is a notified
    deductor (government / PSU). We pass that as a boolean
    ``gst_tds_deductor`` flag — sourced from ``clients.gst_tds_deductor``.
  * The single contract value exceeds ₹2.5 lakh (₹250,000) — we compare
    against the transaction's gross taxable value.

When both gates pass, the TDS amount is **2% of the taxable value**
(1% CGST + 1% SGST or 2% IGST). The verdict surfaces this as
``tds_amount`` and the reasons array records ``section_51``.

> Income-Tax Act TDS (194C / 194J / 194H / …) is out of scope for v1
> per ADR-005 rev 2.

Pure Python. No LLM. Decimal arithmetic only.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.prompts.tax_law_library import GST_TDS_THRESHOLDS
from app.tax.types import Transaction
from app.utils.decimal_utils import ZERO, quantize_money

__all__ = [
    "TdsResult",
    "compute_tds",
]


@dataclass(frozen=True)
class TdsResult:
    """Outcome of the GST-TDS check."""

    tds_amount: Decimal
    applied: bool
    reason: str | None = None


def compute_tds(transaction: Transaction, *, gst_tds_deductor: bool) -> TdsResult:
    """Compute GST-TDS for ``transaction``.

    Returns zero with ``applied=False`` and a structured reason when any
    gate fails — the verdict carries the reason so a CA can see *why*
    GST-TDS was (not) applied without re-running the engine.
    """
    if not gst_tds_deductor:
        return TdsResult(tds_amount=ZERO, applied=False, reason="recipient_not_deductor")
    taxable = transaction.gross_taxable_value
    if taxable < GST_TDS_THRESHOLDS.threshold_inr:
        return TdsResult(
            tds_amount=ZERO,
            applied=False,
            reason="below_threshold",
        )
    amount = quantize_money(taxable * GST_TDS_THRESHOLDS.rate_percent / Decimal(100))
    return TdsResult(tds_amount=amount, applied=True, reason="section_51")
