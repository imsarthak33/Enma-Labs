"""TA-2 phase 2 tests — bank-statement parser + 180-day matcher.

Both pure/DB-free. The bank CSV is hand-built to common Indian-bank column
layouts (no real statement fixture yet); validate on first upload.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from app.services.bank_import import (
    BankStatementParseError,
    BankTxn,
    looks_like_bank_csv,
    parse_bank_statement,
)
from app.services.bank_recon import PurchaseForPayment, reconcile_payments

# HDFC-style: a preamble line, then headers with "Withdrawal Amt."/"Deposit Amt.".
_HDFC_CSV = (
    b"Statement for account 5012345678 - ACME BUYER PVT LTD\n"
    b"Date,Narration,Withdrawal Amt.,Deposit Amt.,Closing Balance\n"
    b"17/08/2025,NEFT-ACME SUPPLIES-UTR123,11800.00,,50000.00\n"
    b"18/08/2025,SALARY CREDIT,,30000.00,80000.00\n"
    b"20/08/2025,CHQ 456-WIDGETS CO,5900.00,,74100.00\n"
)

# ICICI-style: Debit/Credit columns, dd-mm-yyyy.
_ICICI_CSV = (
    b"Txn Date,Particulars,Debit,Credit,Balance\n"
    b"15-08-2025,RTGS ACME SUPPLIES,11800.00,0.00,40000.00\n"
)


# ── parser ──────────────────────────────────────────────────────────────


def test_sniff_recognises_bank_csv() -> None:
    assert looks_like_bank_csv(_HDFC_CSV) is True
    assert looks_like_bank_csv(_ICICI_CSV) is True


def test_sniff_rejects_non_bank() -> None:
    assert looks_like_bank_csv(b'{"data":{"docdata":{}}}') is False
    assert looks_like_bank_csv(b"<ENVELOPE></ENVELOPE>") is False
    assert looks_like_bank_csv(b"Bucket,Supplier GSTIN,Invoice No\nmatched,X,1") is False


def test_parse_hdfc_with_preamble() -> None:
    stmt = parse_bank_statement(_HDFC_CSV)
    assert len(stmt.transactions) == 3
    debit = stmt.transactions[0]
    assert debit.txn_date == date(2025, 8, 17)
    assert debit.amount == Decimal("11800.00")
    assert debit.direction == "debit"
    assert "ACME SUPPLIES" in debit.narration
    credit = stmt.transactions[1]
    assert credit.direction == "credit"
    assert credit.amount == Decimal("30000.00")


def test_parse_icici_debit_credit_columns() -> None:
    stmt = parse_bank_statement(_ICICI_CSV)
    assert len(stmt.transactions) == 1
    t = stmt.transactions[0]
    assert t.txn_date == date(2025, 8, 15)
    assert t.amount == Decimal("11800.00")
    assert t.direction == "debit"


def test_parse_rejects_garbage() -> None:
    with pytest.raises(BankStatementParseError):
        parse_bank_statement(b"just,some,random\n1,2,3")


# ── 180-day matcher ───────────────────────────────────────────────────────


def _purchase(vendor: str, d: date, total: str, itc: str) -> PurchaseForPayment:
    return PurchaseForPayment(
        ref=vendor[:8],
        vendor_name=vendor,
        invoice_date=d,
        grand_total=Decimal(total),
        itc_amount=Decimal(itc),
    )


def _debit(narration: str, d: date, amount: str) -> BankTxn:
    return BankTxn(txn_date=d, narration=narration, amount=Decimal(amount), direction="debit")


def test_paid_within_180() -> None:
    purchases = [_purchase("ACME SUPPLIES", date(2025, 8, 15), "11800.00", "1800.00")]
    debits = [_debit("NEFT-ACME SUPPLIES-UTR123", date(2025, 8, 17), "11800.00")]
    r = reconcile_payments(purchases=purchases, debits=debits, as_of=date(2026, 6, 24))
    assert r.paid_count == 1
    assert r.unpaid_over_180_count == 0
    assert r.reversal_risk_itc == Decimal("0")


def test_unpaid_over_180_is_reversal_risk() -> None:
    # Invoice from Jan 2025, no payment, as-of mid-2026 → past 180 days.
    purchases = [_purchase("WIDGETS CO", date(2025, 1, 1), "5900.00", "900.00")]
    r = reconcile_payments(purchases=purchases, debits=[], as_of=date(2026, 6, 24))
    assert r.unpaid_over_180_count == 1
    assert r.reversal_risk_itc == Decimal("900.00")


def test_unpaid_within_180_watch() -> None:
    # Recent invoice, no payment yet, still inside the window.
    purchases = [_purchase("FRESH VENDOR", date(2026, 6, 1), "1180.00", "180.00")]
    r = reconcile_payments(purchases=purchases, debits=[], as_of=date(2026, 6, 24))
    assert r.unpaid_within_180_count == 1
    assert r.unpaid_over_180_count == 0
    assert r.reversal_risk_itc == Decimal("0")
    assert r.lines[0].days_to_deadline is not None


def test_no_match_on_wrong_amount() -> None:
    purchases = [_purchase("ACME SUPPLIES", date(2025, 8, 15), "11800.00", "1800.00")]
    debits = [_debit("NEFT-ACME SUPPLIES", date(2025, 8, 17), "9999.00")]  # wrong amount
    r = reconcile_payments(purchases=purchases, debits=debits, as_of=date(2026, 6, 24))
    assert r.paid_count == 0
    assert r.unpaid_over_180_count == 1  # invoice now old → reversal


def test_no_match_on_wrong_vendor() -> None:
    purchases = [_purchase("ACME SUPPLIES", date(2025, 8, 15), "11800.00", "1800.00")]
    debits = [_debit("NEFT-SOMEONE ELSE", date(2025, 8, 17), "11800.00")]
    r = reconcile_payments(purchases=purchases, debits=debits, as_of=date(2026, 6, 24))
    assert r.paid_count == 0


def test_payment_outside_180_window_not_paid() -> None:
    # Payment exists but 200 days after the invoice → still a reversal case.
    purchases = [_purchase("ACME SUPPLIES", date(2025, 1, 1), "11800.00", "1800.00")]
    debits = [_debit("NEFT-ACME SUPPLIES", date(2025, 8, 1), "11800.00")]  # ~212 days later
    r = reconcile_payments(purchases=purchases, debits=debits, as_of=date(2026, 6, 24))
    assert r.paid_count == 0
    assert r.unpaid_over_180_count == 1
