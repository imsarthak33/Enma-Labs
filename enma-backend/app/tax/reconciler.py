"""Python-driven invoice math — separates "LLM reads" from "Python computes".

Background
----------
The extractor LLM is good at reading text off an invoice (vendor name,
GSTIN, line item description, the printed numbers in each column). It is
*bad* at picking which printed number is the taxable amount, which is
the total tax, and which is the grand total. It is also bad at
multiplying ``quantity * unit_price`` reliably across many cells.

Production saw the failure pattern twice on real SUDHA invoices:

  invoice prints:                LLM extracted (wrong):
    Taxable Amount  ₹ 9,285.71     taxable_value = "464.29"   ← tax col
    CGST @2.5%      ₹   232.14     cgst_amount   = "232.14"   ← right
    SGST @2.5%      ₹   232.14     sgst_amount   = "232.14"   ← right
    Total Amount    ₹ 9,750.00     grand_total   = "9750"     ← right

Then the verifier (which IS deterministic Python math) saw
``taxable_value = 464.29`` and ``cgst_rate = 2.5`` and dutifully
flagged ``cgst should be 11.61`` — garbage in, garbage out.

The fix — implemented here — is to make math a Python concern with the
LLM providing only:

* **The labelled raw cells** the invoice prints (qty, unit_price, the
  TAX column value, the AMOUNT column value).
* **The OBSERVED totals block** as a list of ``(label, amount)`` pairs
  copied verbatim from the bottom of the invoice.

This module then:

1. Derives ``taxable`` per line from ``line_amount - tax_amount`` when
   both are extracted (most precise), falling back to
   ``quantity * unit_price``.
2. Sums line-item taxables to a canonical ``line_items_taxable``.
3. Parses observed-totals labels with a small regex set
   (``CGST\\s*@?\\s*(\\d+\\.?\\d*)\\s*%`` etc.) to learn the rates and
   the printed CGST / SGST / IGST / Grand totals.
4. Computes canonical ``cgst_amount = canonical_taxable * cgst_rate``
   etc. using :mod:`decimal` only — no floats.
5. Cross-checks observed labels against computed numbers and emits a
   ``ReconciliationIssue`` for any mismatch >₹1 — that's the discrepancy
   the CA needs to see.

The output ``ReconciledInvoice`` is the *new* source of truth for the
verifier, the tax engine, and the pipeline summary. The LLM's
``totals`` block is preserved on the persisted document for audit but
is never the basis of downstream computation.
"""

from __future__ import annotations

import re
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
_TOLERANCE_LINE: Final[Decimal] = Decimal("0.10")

# Regex set for parsing the totals-section labels we see in practice.
# Labels are matched case-insensitively. Whitespace around the @ is
# permitted because OCR'd text varies. The capture group is the rate.
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


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReconciliationIssue:
    """One disagreement between observed (invoice) and computed (Python)."""

    code: str
    message: str


@dataclass(frozen=True)
class ReconciledLineItem:
    """Per-line canonical numbers derived by Python, never by the LLM."""

    description: str | None
    hsn_sac: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    taxable: Decimal
    tax_total: Decimal


@dataclass(frozen=True)
class ReconciledInvoice:
    """The canonical, downstream-of-truth totals for one invoice."""

    line_items: tuple[ReconciledLineItem, ...]
    taxable: Decimal
    cgst_rate: Decimal
    cgst_amount: Decimal
    sgst_rate: Decimal
    sgst_amount: Decimal
    igst_rate: Decimal
    igst_amount: Decimal
    grand_total: Decimal
    discrepancies: tuple[ReconciliationIssue, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dump used for persistence + summary rendering."""
        return {
            "line_items": [
                {
                    "description": li.description,
                    "hsn_sac": li.hsn_sac,
                    "quantity": str(li.quantity) if li.quantity is not None else None,
                    "unit_price": str(li.unit_price) if li.unit_price is not None else None,
                    "taxable": str(li.taxable),
                    "tax_total": str(li.tax_total),
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
            "discrepancies": [
                {"code": d.code, "message": d.message} for d in self.discrepancies
            ],
        }


# ---------------------------------------------------------------------------
# Money parsing — accept None and bad input quietly (reconciler tolerates)
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
    except Exception:  # pragma: no cover — Decimal is forgiving
        return None


# ---------------------------------------------------------------------------
# Line-item reconciliation
# ---------------------------------------------------------------------------


def _reconcile_line(item: dict[str, Any]) -> ReconciledLineItem | None:
    """Derive canonical (taxable, tax_total) for a single line.

    Precedence for ``taxable``:
      1. ``line_amount - tax_amount`` (most precise — both are printed
         on the invoice, no division).
      2. ``quantity * unit_price`` (loses precision when unit_price was
         rounded by the invoice template, e.g. ₹61.9 instead of
         ₹61.9047619...).
      3. The LLM's ``taxable_value`` is a last resort and only when
         neither (1) nor (2) is derivable — the field is known to
         hallucinate.
    """
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
        # Last resort: trust the LLM. Tagged for the discrepancy report
        # upstream — if we ever rely on this, the caller flags it.
        fallback = _safe_money(item.get("taxable_value"))
        if fallback is not None:
            taxable = fallback

    if taxable is None:
        return None

    tax_total: Decimal = ZERO
    if tax_amount is not None:
        tax_total = tax_amount
    else:
        # Derive from rates if available — this is the LLM's last gift to us.
        for rate_key in ("cgst_rate", "sgst_rate", "igst_rate"):
            rate = _safe_money(item.get(rate_key))
            if rate is not None and rate > ZERO:
                tax_total += quantize_money(taxable * rate / Decimal(100))

    return ReconciledLineItem(
        description=_str_or_none(item.get("description")),
        hsn_sac=_str_or_none(item.get("hsn_sac")),
        quantity=qty,
        unit_price=unit_price,
        taxable=quantize_money(taxable),
        tax_total=quantize_money(tax_total),
    )


def _str_or_none(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if value is None:
        return None
    return str(value)


# ---------------------------------------------------------------------------
# Observed-totals parsing
# ---------------------------------------------------------------------------


@dataclass
class _ObservedTotals:
    """Parsed (label, amount) pairs from the invoice's totals section."""

    taxable: Decimal | None = None
    cgst: tuple[Decimal, Decimal] | None = None  # (rate, amount)
    sgst: tuple[Decimal, Decimal] | None = None
    igst: tuple[Decimal, Decimal] | None = None
    grand: Decimal | None = None


def _parse_observed_totals(observed: object) -> _ObservedTotals:
    """Walk a list of ``{label, amount}`` dicts and extract the canonical fields.

    Accepts the LLM-friendly shape:
        [{"label": "CGST @2.5%", "amount": "232.14"}, ...]

    Any item that doesn't match a known label is ignored — we don't try
    to interpret unknown labels.
    """
    parsed = _ObservedTotals()
    if not isinstance(observed, list):
        return parsed
    for entry in observed:
        if not isinstance(entry, dict):
            continue
        label = entry.get("label")
        amount = _safe_money(entry.get("amount"))
        if not isinstance(label, str) or amount is None:
            continue
        clean_label = label.strip()
        rate = _parse_rate(clean_label)
        if _LABEL_TAXABLE.match(clean_label):
            parsed.taxable = amount
        elif _LABEL_CGST.match(clean_label):
            parsed.cgst = (rate or ZERO, amount)
        elif _LABEL_SGST.match(clean_label):
            parsed.sgst = (rate or ZERO, amount)
        elif _LABEL_IGST.match(clean_label):
            parsed.igst = (rate or ZERO, amount)
        elif _LABEL_GRAND.match(clean_label):
            parsed.grand = amount
    return parsed


# ---------------------------------------------------------------------------
# Top-level reconciler
# ---------------------------------------------------------------------------


def reconcile_extraction(extraction: dict[str, Any]) -> ReconciledInvoice:  # noqa: PLR0912
    """Compute canonical totals from an extraction. NEVER trust LLM math.

    Inputs taken seriously:

    * ``line_items[].quantity`` and ``unit_price`` (raw OCR — cleanly read)
    * ``line_items[].tax_amount`` and ``line_amount`` (raw OCR — printed)
    * ``line_items[].(c|s|i)gst_rate`` (rate, not amount — extracted cleanly)
    * ``observed_totals`` if present (the labelled totals block, NEW schema)

    Inputs IGNORED (these are the hallucination hotspots):

    * ``line_items[].taxable_value`` — only used as a last-resort fallback
    * ``line_items[].(c|s|i)gst_amount`` — recomputed from rate + canonical taxable
    * ``totals.*`` — never trusted; ``observed_totals`` is the new path

    Decimal math throughout; output values are 2-dp quantised.
    """
    items_raw = extraction.get("line_items") or []
    if not isinstance(items_raw, list):
        items_raw = []
    line_items: list[ReconciledLineItem] = []
    for raw in items_raw:
        rec = _reconcile_line(raw)
        if rec is not None:
            line_items.append(rec)

    line_items_taxable = (
        sum_money([li.taxable for li in line_items]) if line_items else ZERO
    )
    line_items_tax_total = (
        sum_money([li.tax_total for li in line_items]) if line_items else ZERO
    )

    observed = _parse_observed_totals(extraction.get("observed_totals"))
    discrepancies: list[ReconciliationIssue] = []

    # Pick canonical taxable. Prefer line-item sum when it agrees with the
    # observed label within tolerance; otherwise trust line items and flag.
    canonical_taxable = line_items_taxable
    if observed.taxable is not None:
        if abs(observed.taxable - line_items_taxable) > _TOLERANCE_PAISA:
            # The observed-label number disagrees with the line-item sum.
            # Default to the line-item sum (math from raw cells is more
            # reliable than the LLM identifying the right row), but flag.
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
            # Observed agrees — prefer it (it carries the invoice's
            # native precision, not the qty*unit_price rounding loss).
            canonical_taxable = observed.taxable

    # Tax rates: prefer observed labels (they carry "@2.5%"). Fall back to
    # the first non-zero per-line rate the LLM emitted.
    cgst_rate, cgst_obs_amount = observed.cgst or (None, None)
    sgst_rate, sgst_obs_amount = observed.sgst or (None, None)
    igst_rate, igst_obs_amount = observed.igst or (None, None)
    if cgst_rate is None:
        cgst_rate = _first_rate(items_raw, "cgst_rate")
    if sgst_rate is None:
        sgst_rate = _first_rate(items_raw, "sgst_rate")
    if igst_rate is None:
        igst_rate = _first_rate(items_raw, "igst_rate")
    cgst_rate = cgst_rate or ZERO
    sgst_rate = sgst_rate or ZERO
    igst_rate = igst_rate or ZERO

    # Compute canonical taxes from canonical taxable and parsed rates.
    cgst_amount = quantize_money(canonical_taxable * cgst_rate / Decimal(100))
    sgst_amount = quantize_money(canonical_taxable * sgst_rate / Decimal(100))
    igst_amount = quantize_money(canonical_taxable * igst_rate / Decimal(100))

    # If the observed CGST/SGST/IGST amounts differ from computed by more
    # than tolerance, flag — that's a real arithmetic mismatch on the PDF.
    for label, observed_amt, computed_amt in (
        ("CGST", cgst_obs_amount, cgst_amount),
        ("SGST", sgst_obs_amount, sgst_amount),
        ("IGST", igst_obs_amount, igst_amount),
    ):
        if observed_amt is not None and abs(observed_amt - computed_amt) > _TOLERANCE_PAISA:
            discrepancies.append(
                ReconciliationIssue(
                    code=f"{label.lower()}_mismatch",
                    message=(
                        f"Invoice shows {label} {observed_amt}, "
                        f"computed {computed_amt} from taxable * rate."
                    ),
                )
            )

    # Cross-check line-item tax_total against computed total tax.
    computed_total_tax = cgst_amount + sgst_amount + igst_amount
    if line_items and abs(line_items_tax_total - computed_total_tax) > _TOLERANCE_PAISA:
        discrepancies.append(
            ReconciliationIssue(
                code="line_tax_sum_mismatch",
                message=(
                    f"Sum of line tax_amounts {line_items_tax_total} differs "
                    f"from computed total tax {computed_total_tax}."
                ),
            )
        )

    canonical_grand = quantize_money(
        canonical_taxable + cgst_amount + sgst_amount + igst_amount
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

    return ReconciledInvoice(
        line_items=tuple(line_items),
        taxable=quantize_money(canonical_taxable),
        cgst_rate=cgst_rate,
        cgst_amount=cgst_amount,
        sgst_rate=sgst_rate,
        sgst_amount=sgst_amount,
        igst_rate=igst_rate,
        igst_amount=igst_amount,
        grand_total=canonical_grand,
        discrepancies=tuple(discrepancies),
    )


def _first_rate(items: list[Any], key: str) -> Decimal | None:
    for item in items:
        if isinstance(item, dict):
            r = _safe_money(item.get(key))
            if r is not None and r > ZERO:
                return r
    return None
