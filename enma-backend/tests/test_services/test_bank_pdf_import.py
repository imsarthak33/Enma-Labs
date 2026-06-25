"""TA-2 hardening tests — PDF bank-statement parser (ADR-016).

Pure / DB-free. Indian banks ship statements as PDF e-statements, so the
parser reconstructs transactions from the PDF text layer and assigns each a
direction from the running-balance walk (never OCR, never column position).

Fixtures are synthetic PDFs built token-per-line to mirror how PyMuPDF
actually extracts a real statement — no real client data is committed. The
layout (CC account with negative running balance) matches the real SBI
statement this parser was validated against.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pymupdf
import pytest
from app.services.bank_import import BankStatementParseError
from app.services.bank_pdf_import import looks_like_bank_pdf, parse_bank_pdf


def _make_pdf(lines: list[str]) -> bytes:
    """Render lines into a single-column PDF, one line per row, top-to-bottom.

    PyMuPDF's text extraction returns inserted lines in reading order, so
    this reproduces the per-token line layout a real bank PDF yields.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 50.0
    for line in lines:
        if y > 800:
            page = doc.new_page(width=595, height=842)
            y = 50.0
        page.insert_text((40, y), line, fontsize=8)
        y += 11.0
    out: bytes = doc.tobytes()
    doc.close()
    return out


# A CC statement (negative running balance), each field on its own line —
# txn date, value date, narration, branch code, printed amount, balance.
def _txn_lines(
    txn_date: str, narration: str, branch: str, amount: str, balance: str
) -> list[str]:
    return [txn_date, txn_date, narration, branch, amount, balance]


_HEADER = [
    "M/S TEST TRADERS",
    "Account Statement from 1 Jan 2026 to 14 Feb 2026",
    "Balance as on 1 Jan 2026  :\t-4,82,803.51",
    "Txn Date Value Date Description Ref Branch Debit Credit Balance",
]

_STATEMENT_PDF = _make_pdf(
    _HEADER
    + _txn_lines("02/01/2026", "BY TRANSFER-UPI ACME SUPPLIES", "88", "25,000.00", "-4,57,803.51")
    + _txn_lines("03/01/2026", "TO TRANSFER-NEFT WIDGETS CO", "4266", "37,742.00", "-4,95,545.51")
    + _txn_lines("05/01/2026", "CASH DEPOSIT SELF", "88", "77,200.00", "-4,18,345.51")
)


# ── sniff precision ───────────────────────────────────────────────────────


def test_sniff_recognises_statement_pdf() -> None:
    assert looks_like_bank_pdf(_STATEMENT_PDF) is True


def test_sniff_rejects_invoice_pdf() -> None:
    # An invoice PDF carries money + dates but NO statement marker — it must
    # stay on the OCR path, not get stolen by the bank leg.
    invoice = _make_pdf(
        [
            "TAX INVOICE",
            "GSTIN: 10CDVPS7198R1Z7",
            "Invoice No INV-001 dated 02/01/2026",
            "Widget x10",
            "Taxable Value 10,000.00",
            "IGST 18% 1,800.00",
            "Grand Total 11,800.00",
        ]
    )
    assert looks_like_bank_pdf(invoice) is False


def test_sniff_rejects_non_pdf() -> None:
    assert looks_like_bank_pdf(b"Date,Narration,Debit,Credit\n01/01/2026,X,1,2") is False
    assert looks_like_bank_pdf(b'{"data":{}}') is False


# ── parse + balance-walk direction ────────────────────────────────────────


def test_parse_assigns_direction_from_balance_walk() -> None:
    stmt = parse_bank_pdf(_STATEMENT_PDF)
    assert len(stmt.transactions) == 3

    t0 = stmt.transactions[0]
    assert t0.txn_date == date(2026, 1, 2)
    assert t0.amount == Decimal("25000.00")
    assert t0.direction == "credit"  # balance rose -482803.51 → -457803.51
    assert "ACME SUPPLIES" in t0.narration

    t1 = stmt.transactions[1]
    assert t1.amount == Decimal("37742.00")
    assert t1.direction == "debit"  # balance fell
    assert "WIDGETS CO" in t1.narration

    t2 = stmt.transactions[2]
    assert t2.amount == Decimal("77200.00")
    assert t2.direction == "credit"


def test_printed_amount_disagreement_trusts_balance_delta() -> None:
    # Printed amount is wrong (OCR-style corruption); the balance delta of
    # 25,000 is authoritative and overrides it.
    pdf = _make_pdf(
        _HEADER
        + _txn_lines("02/01/2026", "TO TRANSFER WIDGETS CO", "88", "99,999.00", "-5,07,803.51")
        + _txn_lines("03/01/2026", "CASH DEPOSIT SELF", "88", "10,000.00", "-4,97,803.51")
    )
    stmt = parse_bank_pdf(pdf)
    t0 = stmt.transactions[0]
    assert t0.direction == "debit"
    assert t0.amount == Decimal("25000.00")  # delta, not the printed 99,999


def test_no_opening_balance_falls_back_to_narration() -> None:
    # Drop the opening-balance line → row 1 has no seed delta and must lean
    # on the printed amount + narration keyword.
    pdf = _make_pdf(
        [
            "M/S TEST TRADERS",
            "Account Statement from 1 Jan 2026",
            "Txn Date Value Date Description Branch Debit Credit Balance",
            *_txn_lines("02/01/2026", "TO TRANSFER WIDGETS CO", "88", "25,000.00", "-5,07,803.51"),
            *_txn_lines("03/01/2026", "BY TRANSFER ACME", "88", "50,000.00", "-4,57,803.51"),
        ]
    )
    stmt = parse_bank_pdf(pdf)
    assert stmt.transactions[0].direction == "debit"  # from "TO TRANSFER" hint
    assert stmt.transactions[0].amount == Decimal("25000.00")
    assert stmt.transactions[1].direction == "credit"  # delta now available


def test_parse_rejects_pdf_without_transactions() -> None:
    pdf = _make_pdf(["Account Statement", "No transactions in this period."])
    with pytest.raises(BankStatementParseError):
        parse_bank_pdf(pdf)


def test_parse_rejects_non_pdf_bytes() -> None:
    with pytest.raises(BankStatementParseError):
        parse_bank_pdf(b"not a pdf at all")
