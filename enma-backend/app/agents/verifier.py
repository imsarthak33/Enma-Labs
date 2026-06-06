"""Deterministic red-team verifier — NO LLM.

The extractor produces a structured JSON blob; this module checks that
blob against rules that don't need a language model:

  1. GSTIN format + checksum on both vendor and buyer.
  2. Tax-rate coexistence: a line item carries EITHER CGST+SGST (intra-
     state) OR IGST (inter-state) — never both.
  3. Per-line math: ``taxable_value * cgst_rate / 100 ≈ cgst_amount``
     (within a 1-paisa tolerance), similarly for SGST and IGST.
  4. Totals math: line-item sums equal the totals block within tolerance.
  5. Grand-total math: ``taxable + cgst + sgst + igst ≈ grand_total``.

All arithmetic uses :class:`decimal.Decimal`. Floats are refused at the
:mod:`app.utils.decimal_utils` boundary.

Why deterministic?
------------------
A CA reviewing the verdict must be able to point at *the function* that
produced a flag. LLM-based verification would mean every audit trace
ends at "the model said so", which is not a defensible position in a
tax filing context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.tax.gstin_validator import validate_gstin
from app.utils.decimal_utils import ZERO, parse_money, quantize_money, sum_money

__all__ = [
    "FieldRef",
    "VerificationIssue",
    "VerificationResult",
    "VerificationSeverity",
    "verify_extraction",
]


# Tolerance for floating-point-like rounding errors in extracted amounts.
# Indian tax authorities accept rounding within 1 paisa per line and 1
# rupee per filing; we use 1 paisa as the verifier tolerance so a CA
# investigating a flag sees the same number the GSTN portal sees.
_PAISA: dict[str, Decimal] = {"tol": Decimal("0.01")}


class VerificationSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True)
class FieldRef:
    """Path to a field inside the extraction JSON, used in issue reports."""

    path: str  # e.g. "vendor.gstin", "line_items[2].cgst_amount"


@dataclass(frozen=True)
class VerificationIssue:
    """One thing the verifier flagged."""

    severity: VerificationSeverity
    code: str  # short, machine-friendly identifier
    message: str  # human-readable, but routed to CAs not end clients
    field: FieldRef | None = None


@dataclass(frozen=True)
class VerificationResult:
    """Aggregate outcome of running the verifier over an extraction."""

    issues: tuple[VerificationIssue, ...] = field(default_factory=tuple)

    @property
    def is_clean(self) -> bool:
        return not any(i.severity is VerificationSeverity.ERROR for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.severity is VerificationSeverity.WARNING for i in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_clean": self.is_clean,
            "has_warnings": self.has_warnings,
            "issues": [
                {
                    "severity": i.severity.value,
                    "code": i.code,
                    "message": i.message,
                    "field": i.field.path if i.field else None,
                }
                for i in self.issues
            ],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_money(value: object) -> Decimal | None:
    """Parse a money value, returning None if it's missing or unparseable."""
    if value is None:
        return None
    try:
        return parse_money(value)
    except (TypeError, ValueError):
        return None


def _close_enough(a: Decimal, b: Decimal, *, tol: Decimal = _PAISA["tol"]) -> bool:
    return abs(a - b) <= tol


def _pct(value: Decimal, rate: Decimal) -> Decimal:
    """``value * rate / 100``, quantised to 2 dp."""
    return quantize_money(value * rate / Decimal(100))


# ---------------------------------------------------------------------------
# Per-stage checks
# ---------------------------------------------------------------------------


def _check_gstins(extraction: dict[str, Any], issues: list[VerificationIssue]) -> None:
    for party in ("vendor", "buyer"):
        party_obj = extraction.get(party) or {}
        if not isinstance(party_obj, dict):
            continue
        gstin = party_obj.get("gstin")
        if gstin is None or gstin == "":
            # B2C invoices legitimately have no buyer GSTIN. We flag at
            # WARNING for vendor, INFO for buyer.
            sev = VerificationSeverity.WARNING if party == "vendor" else VerificationSeverity.INFO
            issues.append(
                VerificationIssue(
                    severity=sev,
                    code=f"{party}_gstin_missing",
                    message=f"No GSTIN found for {party}.",
                    field=FieldRef(f"{party}.gstin"),
                )
            )
            continue
        result = validate_gstin(gstin)
        if not result.is_valid:
            issues.append(
                VerificationIssue(
                    severity=VerificationSeverity.ERROR,
                    code=f"{party}_gstin_invalid",
                    message=(
                        f"{party.capitalize()} GSTIN {gstin!r} failed validation"
                        f" ({result.reason})."
                    ),
                    field=FieldRef(f"{party}.gstin"),
                )
            )


def _check_line_items(extraction: dict[str, Any], issues: list[VerificationIssue]) -> None:
    items = extraction.get("line_items") or []
    if not isinstance(items, list):
        issues.append(
            VerificationIssue(
                severity=VerificationSeverity.ERROR,
                code="line_items_not_list",
                message="line_items is missing or not a list.",
                field=FieldRef("line_items"),
            )
        )
        return

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            issues.append(
                VerificationIssue(
                    severity=VerificationSeverity.ERROR,
                    code="line_item_not_object",
                    message=f"Line item {idx + 1} is not an object.",
                    field=FieldRef(f"line_items[{idx}]"),
                )
            )
            continue
        _check_line_item(idx, item, issues)


def _check_line_item(idx: int, item: dict[str, Any], issues: list[VerificationIssue]) -> None:
    taxable = _safe_money(item.get("taxable_value"))
    cgst_amt = _safe_money(item.get("cgst_amount"))
    sgst_amt = _safe_money(item.get("sgst_amount"))
    igst_amt = _safe_money(item.get("igst_amount"))
    cgst_rate = _safe_money(item.get("cgst_rate"))
    sgst_rate = _safe_money(item.get("sgst_rate"))
    igst_rate = _safe_money(item.get("igst_rate"))

    has_cgst = (cgst_amt is not None and cgst_amt > ZERO) or (
        cgst_rate is not None and cgst_rate > ZERO
    )
    has_sgst = (sgst_amt is not None and sgst_amt > ZERO) or (
        sgst_rate is not None and sgst_rate > ZERO
    )
    has_igst = (igst_amt is not None and igst_amt > ZERO) or (
        igst_rate is not None and igst_rate > ZERO
    )

    # Coexistence rule: CGST+SGST XOR IGST. Never both. Either is allowed
    # to be entirely absent (exempt supplies, zero-rated exports, etc.).
    if has_igst and (has_cgst or has_sgst):
        issues.append(
            VerificationIssue(
                severity=VerificationSeverity.ERROR,
                code="tax_coexistence",
                message=(
                    f"Line {idx + 1}: IGST cannot coexist with CGST/SGST on the" " same line."
                ),
                field=FieldRef(f"line_items[{idx}]"),
            )
        )

    # CGST must always be paired with SGST and vice versa.
    if has_cgst != has_sgst:
        issues.append(
            VerificationIssue(
                severity=VerificationSeverity.ERROR,
                code="cgst_sgst_mismatch",
                message=(
                    f"Line {idx + 1}: CGST and SGST must appear together " "(intra-state supply)."
                ),
                field=FieldRef(f"line_items[{idx}]"),
            )
        )

    if taxable is None:
        issues.append(
            VerificationIssue(
                severity=VerificationSeverity.WARNING,
                code="missing_taxable_value",
                message=f"Line {idx + 1}: taxable_value is missing.",
                field=FieldRef(f"line_items[{idx}].taxable_value"),
            )
        )
        return

    # Per-line math: rate * taxable / 100 ~= amount.
    for rate, amount, label in (
        (cgst_rate, cgst_amt, "cgst"),
        (sgst_rate, sgst_amt, "sgst"),
        (igst_rate, igst_amt, "igst"),
    ):
        if rate is not None and amount is not None:
            expected = _pct(taxable, rate)
            if not _close_enough(expected, amount):
                issues.append(
                    VerificationIssue(
                        severity=VerificationSeverity.ERROR,
                        code=f"{label}_math_mismatch",
                        message=(
                            f"Line {idx + 1}: {label.upper()} should be "
                            f"{expected}, got {amount}."
                        ),
                        field=FieldRef(f"line_items[{idx}].{label}_amount"),
                    )
                )


def _check_totals(extraction: dict[str, Any], issues: list[VerificationIssue]) -> None:
    items = extraction.get("line_items") or []
    totals = extraction.get("totals") or {}
    if not isinstance(totals, dict):
        issues.append(
            VerificationIssue(
                severity=VerificationSeverity.ERROR,
                code="totals_not_object",
                message="totals block is missing or not an object.",
                field=FieldRef("totals"),
            )
        )
        return

    parsed_items = [it for it in items if isinstance(it, dict)]
    sums = {
        "taxable_value": sum_money(
            [_safe_money(it.get("taxable_value")) or ZERO for it in parsed_items]
        ),
        "total_cgst": sum_money(
            [_safe_money(it.get("cgst_amount")) or ZERO for it in parsed_items]
        ),
        "total_sgst": sum_money(
            [_safe_money(it.get("sgst_amount")) or ZERO for it in parsed_items]
        ),
        "total_igst": sum_money(
            [_safe_money(it.get("igst_amount")) or ZERO for it in parsed_items]
        ),
    }

    for key, summed in sums.items():
        claimed = _safe_money(totals.get(key))
        if claimed is None:
            continue  # silently skip; the missing-field warning lives elsewhere
        if not _close_enough(summed, claimed):
            issues.append(
                VerificationIssue(
                    severity=VerificationSeverity.ERROR,
                    code=f"{key}_sum_mismatch",
                    message=(
                        f"{key} should sum to {summed} (from line items), "
                        f"but totals block reports {claimed}."
                    ),
                    field=FieldRef(f"totals.{key}"),
                )
            )

    grand = _safe_money(totals.get("grand_total"))
    if grand is not None:
        expected_grand = quantize_money(
            sums["taxable_value"] + sums["total_cgst"] + sums["total_sgst"] + sums["total_igst"]
        )
        if not _close_enough(grand, expected_grand):
            issues.append(
                VerificationIssue(
                    severity=VerificationSeverity.ERROR,
                    code="grand_total_mismatch",
                    message=(f"grand_total should be {expected_grand}, got {grand}."),
                    field=FieldRef("totals.grand_total"),
                )
            )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def verify_extraction(extraction: object) -> VerificationResult:
    """Run every deterministic check against ``extraction``.

    The signature accepts ``object`` rather than ``dict[str, Any]`` because
    the input can arrive from JSON deserialisation as anything — the
    isinstance gate at the top is the actual type narrowing.
    """
    issues: list[VerificationIssue] = []
    if not isinstance(extraction, dict):
        issues.append(
            VerificationIssue(
                severity=VerificationSeverity.ERROR,
                code="extraction_not_object",
                message="extraction payload is not a JSON object.",
            )
        )
        return VerificationResult(issues=tuple(issues))

    extraction_dict: dict[str, Any] = extraction
    _check_gstins(extraction_dict, issues)
    _check_line_items(extraction_dict, issues)
    _check_totals(extraction_dict, issues)
    return VerificationResult(issues=tuple(issues))
