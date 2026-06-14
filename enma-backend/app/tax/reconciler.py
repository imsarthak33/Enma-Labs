"""Python-driven invoice math — separates "LLM reads" from "Python computes".

Background
----------
The extractor LLM is good at reading text off an invoice (vendor name,
GSTIN, line item description, the printed numbers in each column). It is
*bad* at picking which printed number is the taxable amount, which is
the total tax, and which is the grand total. It is also bad at
multiplying ``quantity * unit_price`` reliably across many cells and
at allocating tax to the right bucket (CGST + SGST vs IGST).

Production saw two distinct failure modes:

1. **Hallucinated math on single-rate invoices.**  Taxable read as the
   tax column (₹464.29 instead of ₹9,285.71). Fixed in v1.

2. **Mixed-rate invoices: v1 of the reconciler trusted ONE rate.**
   An invoice with both 5% and 18% line items has two CGST labels
   in the totals block ("CGST @2.5%", "CGST @9%"). v1 picked the
   last one and applied it to the entire taxable, inflating CGST
   by 5×. It also accepted both CGST+SGST AND IGST rates on the
   same line when the LLM hallucinated → impossible ``tax_coexistence``.

v2 (this module) does math the way Indian GST actually works:

* **Per line**, derive ``taxable`` from ``line_amount - tax_amount``
  (precise) or fall back to ``quantity * unit_price``.
* **Per line**, derive the *single* line tax rate (``5%``, ``18%`` …)
  from ``tax_amount / taxable`` if both are known, else from a
  ``tax_label`` string, else from the LLM-emitted rate columns.
* **Per invoice**, detect ``intra_state`` from the first two characters
  of vendor and buyer GSTINs (which encode the state). Mismatch →
  IGST; match → CGST + SGST split half-and-half.
* **Aggregate** by summing per-line CGST / SGST / IGST. Totals carry
  the natural multi-rate breakdown out the other side; the summary
  surfaces it as ``CGST 1,174.49 (713.81 @ 2.5% + 460.68 @ 9%)``.
* **Cross-check** by *summing* all CGST labels in the observed totals
  block (multiple slices) and comparing to computed total CGST.

The output ``ReconciledInvoice`` is the *new* source of truth for the
verifier, the tax engine, and the pipeline summary. The LLM's
``totals`` block is preserved on the persisted document for audit but
is never the basis of downstream computation.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final

from app.utils.decimal_utils import ZERO, parse_money, quantize_money, sum_money

__all__ = [
    "ReconciledInvoice",
    "ReconciledLineItem",
    "ReconciliationIssue",
    "reconcile_extraction",
]


# Tolerance for cross-checking observed labels against computed canonical
# values. 1 paisa per line, ~1 rupee across the whole invoice. Anything
# beyond this is a real arithmetic disagreement worth surfacing.
_TOLERANCE_PAISA: Final[Decimal] = Decimal("1.00")

# Regex set for parsing the totals-section labels we see in practice.
# Labels are matched case-insensitively. The capture group is the rate.
_RATE_RE: Final[re.Pattern[str]] = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_LABEL_TAXABLE: Final[re.Pattern[str]] = re.compile(
    r"^(?:taxable\s+(?:amount|value)|sub\s*total|net\s+(?:amount|value)|"
    r"basic\s+(?:amount|value)|total\s+taxable)\s*$",
    re.IGNORECASE,
)
_LABEL_CGST: Final[re.Pattern[str]] = re.compile(r"^cgst\b", re.IGNORECASE)
_LABEL_SGST: Final[re.Pattern[str]] = re.compile(r"^sgst\b", re.IGNORECASE)
_LABEL_IGST: Final[re.Pattern[str]] = re.compile(r"^igst\b", re.IGNORECASE)
_LABEL_GRAND: Final[re.Pattern[str]] = re.compile(
    r"^(?:grand\s+total|total\s+(?:amount|invoice\s+value)|invoice\s+total)\s*$",
    re.IGNORECASE,
)

# A GSTIN's first two characters are the state code (``10`` = Bihar,
# ``27`` = Maharashtra, etc.). Intra-state when vendor and buyer share
# the prefix; inter-state otherwise.
_GSTIN_STATE_LEN: Final[int] = 2


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReconciliationIssue:
    code: str
    message: str


@dataclass(frozen=True)
class ReconciledLineItem:
    description: str | None
    hsn_sac: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    taxable: Decimal
    tax_rate: Decimal           # total tax % on this line (e.g. 5, 18)
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal


@dataclass(frozen=True)
class ReconciledInvoice:
    line_items: tuple[ReconciledLineItem, ...]
    taxable: Decimal
    # Aggregate buckets — sums of the per-line amounts above. The
    # *_rate fields are the WEIGHTED-MAJORITY rate where one applies,
    # or ZERO when no single rate dominates (multi-rate invoice).
    cgst_rate: Decimal
    cgst_amount: Decimal
    sgst_rate: Decimal
    sgst_amount: Decimal
    igst_rate: Decimal
    igst_amount: Decimal
    grand_total: Decimal
    # Per-rate breakdown (``{rate -> (cgst_share, sgst_share, igst_share)}``)
    # so the summary can render "CGST 1,174.49 (713.81 @ 2.5% + 460.68 @ 9%)".
    rate_breakdown: tuple[tuple[Decimal, Decimal, Decimal, Decimal], ...] = field(
        default_factory=tuple
    )
    intra_state: bool = True
    discrepancies: tuple[ReconciliationIssue, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_items": [
                {
                    "description": li.description,
                    "hsn_sac": li.hsn_sac,
                    "quantity": str(li.quantity) if li.quantity is not None else None,
                    "unit_price": str(li.unit_price) if li.unit_price is not None else None,
                    "taxable": str(li.taxable),
                    "tax_rate": str(li.tax_rate),
                    "cgst_amount": str(li.cgst_amount),
                    "sgst_amount": str(li.sgst_amount),
                    "igst_amount": str(li.igst_amount),
                }
                for li in self.line_items
            ],
            "taxable": str(self.taxable),
            "cgst_rate": str(self.cgst_rate),
            "cgst_amount": str(self.cgst_amount),
            "sgst_rate": str(self.sgst_rate),
            "sgst_amount": str(self.sgst_amount),
            "igst_rate": str(self.igst_rate),
            "igst_amount": str(self.igst_amount),
            "grand_total": str(self.grand_total),
            "intra_state": self.intra_state,
            "rate_breakdown": [
                {
                    "rate": str(rate),
                    "cgst": str(cgst),
                    "sgst": str(sgst),
                    "igst": str(igst),
                }
                for rate, cgst, sgst, igst in self.rate_breakdown
            ],
            "discrepancies": [
                {"code": d.code, "message": d.message} for d in self.discrepancies
            ],
        }


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _safe_money(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return parse_money(value)
    except (TypeError, ValueError):
        return None


def _parse_rate(text: object) -> Decimal | None:
    """Extract a percentage rate from text like ``"5%"`` or ``"CGST @2.5%"``."""
    if text is None:
        return None
    match = _RATE_RE.search(str(text))
    if match is None:
        return None
    try:
        return Decimal(match.group(1))
    except Exception:  # pragma: no cover
        return None


def _str_or_none(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if value is None:
        return None
    return str(value)


def _state_code(gstin: object) -> str | None:
    """GSTIN's first two characters are the state code."""
    if not isinstance(gstin, str):
        return None
    clean = gstin.strip().upper()
    if len(clean) < _GSTIN_STATE_LEN:
        return None
    prefix = clean[:_GSTIN_STATE_LEN]
    if not prefix.isdigit():
        return None
    return prefix


def _detect_intra_state(extraction: dict[str, Any]) -> bool:
    """True iff vendor and buyer carry the same state code. Defaults True.

    When either GSTIN is missing or unreadable we fall back to intra-state —
    that's the more common case for the CA firms we serve, and the
    reconciler's discrepancy checks will catch a wrong allocation against
    the observed labels.
    """
    vendor_gstin = (extraction.get("vendor") or {}).get("gstin")
    buyer_gstin = (extraction.get("buyer") or {}).get("gstin")
    vs = _state_code(vendor_gstin)
    bs = _state_code(buyer_gstin)
    if vs is None or bs is None:
        return True
    return vs == bs


# ---------------------------------------------------------------------------
# Per-line tax rate derivation
# ---------------------------------------------------------------------------


def _derive_line_rate(
    item: dict[str, Any], taxable: Decimal, tax_amount: Decimal | None
) -> Decimal:
    """Derive the TOTAL tax rate on a single line.

    Precedence:
      1. ``tax_amount / taxable * 100`` rounded to the nearest 0.5
         (so 17.99% snaps to 18, 4.97% snaps to 5). This is the most
         reliable signal because both inputs are read straight off
         the printed cells.
      2. The numeric rate in a ``tax_label`` string (e.g. ``"5%"``,
         ``"(18%)"`` — what the invoice prints under the tax column).
      3. Sum of the LLM-emitted ``cgst_rate + sgst_rate`` (intra-state
         convention: each is half of total).
      4. The LLM-emitted ``igst_rate`` (inter-state, single bucket).

    Returns ZERO when no rate can be inferred — the line is then
    treated as exempt/zero-rated.
    """
    if taxable > ZERO and tax_amount is not None and tax_amount >= ZERO:
        raw = tax_amount / taxable * Decimal(100)
        # Snap to the nearest 0.5% — handles 17.99 → 18, 5.001 → 5.
        snapped = (raw * Decimal(2)).quantize(Decimal("1")) / Decimal(2)
        if snapped >= ZERO:
            return snapped

    label_rate = _parse_rate(item.get("tax_label"))
    if label_rate is not None:
        return label_rate

    cgst = _safe_money(item.get("cgst_rate")) or ZERO
    sgst = _safe_money(item.get("sgst_rate")) or ZERO
    if cgst > ZERO or sgst > ZERO:
        return cgst + sgst

    igst = _safe_money(item.get("igst_rate"))
    if igst is not None and igst > ZERO:
        return igst

    return ZERO


def _reconcile_line(item: dict[str, Any], *, intra_state: bool) -> ReconciledLineItem | None:
    """Derive canonical numbers for a single line, given the state mode."""
    if not isinstance(item, dict):
        return None
    qty = _safe_money(item.get("quantity"))
    unit_price = _safe_money(item.get("unit_price"))
    line_amount = _safe_money(item.get("line_amount"))
    tax_amount = _safe_money(item.get("tax_amount"))

    taxable: Decimal | None = None
    if line_amount is not None and tax_amount is not None:
        taxable = line_amount - tax_amount
    elif qty is not None and unit_price is not None:
        taxable = qty * unit_price
    else:
        fallback = _safe_money(item.get("taxable_value"))
        if fallback is not None:
            taxable = fallback

    if taxable is None or taxable < ZERO:
        return None

    rate = _derive_line_rate(item, taxable, tax_amount)
    # Allocate the line's tax according to state mode. We compute the
    # *amount* from canonical_taxable × rate rather than trusting the
    # LLM's per-tax-bucket figures (which is exactly where v1 went
    # wrong on mixed-rate invoices).
    if intra_state:
        half_rate = rate / Decimal(2)
        cgst_amount = quantize_money(taxable * half_rate / Decimal(100))
        sgst_amount = quantize_money(taxable * half_rate / Decimal(100))
        igst_amount = ZERO
    else:
        cgst_amount = ZERO
        sgst_amount = ZERO
        igst_amount = quantize_money(taxable * rate / Decimal(100))

    return ReconciledLineItem(
        description=_str_or_none(item.get("description")),
        hsn_sac=_str_or_none(item.get("hsn_sac")),
        quantity=qty,
        unit_price=unit_price,
        taxable=quantize_money(taxable),
        tax_rate=rate,
        cgst_amount=cgst_amount,
        sgst_amount=sgst_amount,
        igst_amount=igst_amount,
    )


# ---------------------------------------------------------------------------
# Observed totals parsing — now aware that CGST/SGST/IGST can repeat
# ---------------------------------------------------------------------------


@dataclass
class _ObservedSums:
    taxable: Decimal | None = None
    cgst_total: Decimal | None = None
    sgst_total: Decimal | None = None
    igst_total: Decimal | None = None
    grand: Decimal | None = None


def _parse_observed_totals(observed: object) -> _ObservedSums:
    """Walk the labelled totals block, SUMMING tax rows by bucket.

    A mixed-rate invoice prints multiple CGST rows (``"CGST @2.5%"``
    and ``"CGST @9%"``); both belong to the same bucket and must be
    added. Same for SGST and IGST.
    """
    parsed = _ObservedSums()
    if not isinstance(observed, list):
        return parsed
    for entry in observed:
        if not isinstance(entry, dict):
            continue
        label = entry.get("label")
        amount = _safe_money(entry.get("amount"))
        if not isinstance(label, str) or amount is None:
            continue
        clean = label.strip()
        if _LABEL_TAXABLE.match(clean):
            parsed.taxable = amount
        elif _LABEL_CGST.match(clean):
            parsed.cgst_total = (parsed.cgst_total or ZERO) + amount
        elif _LABEL_SGST.match(clean):
            parsed.sgst_total = (parsed.sgst_total or ZERO) + amount
        elif _LABEL_IGST.match(clean):
            parsed.igst_total = (parsed.igst_total or ZERO) + amount
        elif _LABEL_GRAND.match(clean):
            parsed.grand = amount
    return parsed


# ---------------------------------------------------------------------------
# Top-level reconciler
# ---------------------------------------------------------------------------


def reconcile_extraction(extraction: dict[str, Any]) -> ReconciledInvoice:  # noqa: PLR0912, PLR0915
    """Compute canonical totals from an extraction. NEVER trust LLM math.

    See module docstring for the architectural rationale.
    """
    items_raw = extraction.get("line_items") or []
    if not isinstance(items_raw, list):
        items_raw = []

    intra_state = _detect_intra_state(extraction)

    line_items: list[ReconciledLineItem] = []
    for raw in items_raw:
        rec = _reconcile_line(raw, intra_state=intra_state)
        if rec is not None:
            line_items.append(rec)

    line_items_taxable = (
        sum_money([li.taxable for li in line_items]) if line_items else ZERO
    )
    line_items_cgst = (
        sum_money([li.cgst_amount for li in line_items]) if line_items else ZERO
    )
    line_items_sgst = (
        sum_money([li.sgst_amount for li in line_items]) if line_items else ZERO
    )
    line_items_igst = (
        sum_money([li.igst_amount for li in line_items]) if line_items else ZERO
    )

    observed = _parse_observed_totals(extraction.get("observed_totals"))
    discrepancies: list[ReconciliationIssue] = []

    # Canonical taxable: prefer observed when it matches line items
    # within tolerance (it carries the invoice's native precision);
    # otherwise trust line items and flag.
    canonical_taxable = line_items_taxable
    if observed.taxable is not None:
        if abs(observed.taxable - line_items_taxable) > _TOLERANCE_PAISA:
            discrepancies.append(
                ReconciliationIssue(
                    code="taxable_mismatch",
                    message=(
                        f"Invoice shows taxable {observed.taxable}, "
                        f"line items sum to {line_items_taxable}."
                    ),
                )
            )
        else:
            canonical_taxable = observed.taxable

    # Cross-check the observed CGST / SGST / IGST sums (over all rate slices)
    # against the line-item per-bucket sums.
    for bucket, observed_total, computed_total in (
        ("CGST", observed.cgst_total, line_items_cgst),
        ("SGST", observed.sgst_total, line_items_sgst),
        ("IGST", observed.igst_total, line_items_igst),
    ):
        if observed_total is None:
            continue
        if abs(observed_total - computed_total) > _TOLERANCE_PAISA:
            discrepancies.append(
                ReconciliationIssue(
                    code=f"{bucket.lower()}_mismatch",
                    message=(
                        f"Invoice shows total {bucket} {observed_total}, "
                        f"computed {computed_total} from line items."
                    ),
                )
            )

    canonical_grand = quantize_money(
        canonical_taxable + line_items_cgst + line_items_sgst + line_items_igst
    )
    if observed.grand is not None and abs(observed.grand - canonical_grand) > _TOLERANCE_PAISA:
        discrepancies.append(
            ReconciliationIssue(
                code="grand_total_mismatch",
                message=(
                    f"Invoice shows total {observed.grand}, "
                    f"computed {canonical_grand}."
                ),
            )
        )

    # Group per-line amounts by tax rate so the summary can render
    # "CGST 1,174.49 (713.81 @ 2.5% + 460.68 @ 9%)".
    by_rate: dict[Decimal, tuple[Decimal, Decimal, Decimal]] = defaultdict(
        lambda: (ZERO, ZERO, ZERO)
    )
    for li in line_items:
        cgst, sgst, igst = by_rate[li.tax_rate]
        by_rate[li.tax_rate] = (
            cgst + li.cgst_amount,
            sgst + li.sgst_amount,
            igst + li.igst_amount,
        )
    rate_breakdown = tuple(
        (rate, *amounts) for rate, amounts in sorted(by_rate.items())
    )

    # Pick an aggregate "rate" only when ONE rate dominates the entire
    # invoice — otherwise leave it as ZERO so the summary defers to the
    # breakdown.  This is intentionally cautious because the verifier's
    # legacy per-line ``rate × taxable ≈ amount`` check would mis-fire
    # on a mixed-rate invoice if we surfaced a single rate here.
    if len(by_rate) == 1:
        single_rate = next(iter(by_rate))
        aggregate_rate = single_rate
    else:
        aggregate_rate = ZERO

    return ReconciledInvoice(
        line_items=tuple(line_items),
        taxable=quantize_money(canonical_taxable),
        cgst_rate=aggregate_rate / Decimal(2) if intra_state else ZERO,
        cgst_amount=quantize_money(line_items_cgst),
        sgst_rate=aggregate_rate / Decimal(2) if intra_state else ZERO,
        sgst_amount=quantize_money(line_items_sgst),
        igst_rate=ZERO if intra_state else aggregate_rate,
        igst_amount=quantize_money(line_items_igst),
        grand_total=canonical_grand,
        rate_breakdown=rate_breakdown,
        intra_state=intra_state,
        discrepancies=tuple(discrepancies),
    )
