"""Bank reconciliation — the 180-day ITC-reversal leg (ADR-016).

Pure + deterministic. Matches a client's purchase invoices (the payable =
invoice grand total) against outbound bank debits, then flags invoices
with no payment evidence:

* ``paid``               — a debit matches (amount ±₹1 + vendor name in
                           narration) within 180 days of the invoice.
* ``unpaid_within_180``  — no payment found, but still inside the 180-day
                           window (watch — days_to_deadline given).
* ``unpaid_over_180``    — no payment found and past 180 days → the ITC
                           claimed must be **reversed** (Section 16(2)).

Matching is advisory, never assertive: bank narration carries no GSTIN or
invoice number, so a "no payment found" is exactly that — not proof the
invoice is unpaid. The CA confirms. No LLM in the match loop.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Final

from app.services.bank_import import BankTxn
from app.utils.decimal_utils import ZERO

__all__ = [
    "AMOUNT_TOLERANCE",
    "REVERSAL_WINDOW_DAYS",
    "BankReconLine",
    "BankReconResult",
    "PurchaseForPayment",
    "build_bank_recon_csv",
    "reconcile_payments",
]

AMOUNT_TOLERANCE: Final[Decimal] = Decimal("1.00")
REVERSAL_WINDOW_DAYS: Final[int] = 180
_MIN_TOKEN_LEN: Final[int] = 4

_BUCKET_PAID: Final[str] = "paid"
_BUCKET_UNPAID_WITHIN: Final[str] = "unpaid_within_180"
_BUCKET_UNPAID_OVER: Final[str] = "unpaid_over_180"


@dataclass(frozen=True)
class PurchaseForPayment:
    """A purchase invoice viewed as a payable (for the bank leg)."""

    ref: str
    vendor_name: str
    invoice_date: date | None
    grand_total: Decimal
    itc_amount: Decimal


@dataclass(frozen=True)
class BankReconLine:
    """One classified bank-reconciliation line."""

    bucket: str
    vendor_name: str
    invoice_date: str | None
    grand_total: Decimal
    itc_amount: Decimal
    days_to_deadline: int | None
    note: str


@dataclass(frozen=True)
class BankReconResult:
    """Aggregate bank-reconciliation outcome."""

    lines: tuple[BankReconLine, ...]
    paid_count: int
    unpaid_within_180_count: int
    unpaid_over_180_count: int
    reversal_risk_itc: Decimal

    @property
    def total_lines(self) -> int:
        return len(self.lines)


def _vendor_tokens(name: str) -> set[str]:
    """Significant upper-cased tokens of a vendor name (len ≥ 4)."""
    return {
        tok
        for raw in name.replace("&", " ").split()
        if len(tok := "".join(c for c in raw if c.isalnum()).upper()) >= _MIN_TOKEN_LEN
    }


def _narration_matches_vendor(narration: str, vendor_tokens: set[str]) -> bool:
    """True if any significant vendor token appears in the narration."""
    if not vendor_tokens:
        return False
    upper = narration.upper()
    return any(tok in upper for tok in vendor_tokens)


def reconcile_payments(
    *,
    purchases: list[PurchaseForPayment],
    debits: list[BankTxn],
    as_of: date,
) -> BankReconResult:
    """Match purchases to outbound debits; flag 180-day reversal risk.

    Pure. A debit matches a purchase when the amount is within
    :data:`AMOUNT_TOLERANCE` of the invoice grand total, a significant
    vendor-name token appears in the narration, and the payment falls
    within 180 days of the invoice date. Each debit pays at most one
    invoice (consumed on match).
    """
    used: set[int] = set()
    lines: list[BankReconLine] = []
    paid = within = over = 0
    reversal_itc = ZERO

    only_debits = [t for t in debits if t.direction == "debit"]

    for inv in purchases:
        vendor_tokens = _vendor_tokens(inv.vendor_name)
        matched = False
        if inv.invoice_date is not None:
            deadline = inv.invoice_date + timedelta(days=REVERSAL_WINDOW_DAYS)
            for t in only_debits:
                if id(t) in used:
                    continue
                if abs(t.amount - inv.grand_total) > AMOUNT_TOLERANCE:
                    continue
                if not _narration_matches_vendor(t.narration, vendor_tokens):
                    continue
                if inv.invoice_date <= t.txn_date <= deadline:
                    used.add(id(t))
                    matched = True
                    break

        if matched:
            paid += 1
            lines.append(
                BankReconLine(
                    bucket=_BUCKET_PAID,
                    vendor_name=inv.vendor_name,
                    invoice_date=inv.invoice_date.isoformat() if inv.invoice_date else None,
                    grand_total=inv.grand_total,
                    itc_amount=inv.itc_amount,
                    days_to_deadline=None,
                    note="Payment found within 180 days.",
                )
            )
            continue

        age = (as_of - inv.invoice_date).days if inv.invoice_date is not None else None
        if age is not None and age > REVERSAL_WINDOW_DAYS:
            over += 1
            reversal_itc += inv.itc_amount
            lines.append(
                BankReconLine(
                    bucket=_BUCKET_UNPAID_OVER,
                    vendor_name=inv.vendor_name,
                    invoice_date=inv.invoice_date.isoformat() if inv.invoice_date else None,
                    grand_total=inv.grand_total,
                    itc_amount=inv.itc_amount,
                    days_to_deadline=0,
                    note=(
                        "No payment found and past 180 days — ITC must be "
                        "reversed under Section 16(2) (confirm payment status)."
                    ),
                )
            )
        else:
            within += 1
            remaining = REVERSAL_WINDOW_DAYS - age if age is not None else None
            lines.append(
                BankReconLine(
                    bucket=_BUCKET_UNPAID_WITHIN,
                    vendor_name=inv.vendor_name,
                    invoice_date=inv.invoice_date.isoformat() if inv.invoice_date else None,
                    grand_total=inv.grand_total,
                    itc_amount=inv.itc_amount,
                    days_to_deadline=remaining,
                    note="No payment found yet — still within the 180-day window.",
                )
            )

    return BankReconResult(
        lines=tuple(lines),
        paid_count=paid,
        unpaid_within_180_count=within,
        unpaid_over_180_count=over,
        reversal_risk_itc=reversal_itc,
    )


_BUCKET_ORDER: Final[dict[str, int]] = {
    _BUCKET_UNPAID_OVER: 0,
    _BUCKET_UNPAID_WITHIN: 1,
    _BUCKET_PAID: 2,
}


def build_bank_recon_csv(*, result: BankReconResult, client_name: str) -> bytes:
    """Render the bank-recon result as a utf-8-sig CSV (reversal-risk first)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([f"Bank / 180-day ITC reconciliation — {client_name}"])
    writer.writerow(
        [
            "Status",
            "Vendor",
            "Invoice Date",
            "Grand Total (Rs)",
            "ITC at Risk (Rs)",
            "Days to 180d",
            "Note",
        ]
    )
    for line in sorted(result.lines, key=lambda x: _BUCKET_ORDER.get(x.bucket, 9)):
        writer.writerow(
            [
                line.bucket,
                line.vendor_name,
                line.invoice_date or "",
                f"{line.grand_total}",
                f"{line.itc_amount}",
                "" if line.days_to_deadline is None else line.days_to_deadline,
                line.note,
            ]
        )
    writer.writerow([])
    writer.writerow(["Paid (within 180d)", result.paid_count])
    writer.writerow(["Unpaid (within 180d)", result.unpaid_within_180_count])
    writer.writerow(["Unpaid (over 180d — reversal)", result.unpaid_over_180_count])
    writer.writerow(["ITC at reversal risk (Rs)", f"{result.reversal_risk_itc}"])
    return buf.getvalue().encode("utf-8-sig")
