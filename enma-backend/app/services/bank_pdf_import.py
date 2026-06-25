"""Bank-statement PDF parser — the payment leg's real-world ingress (ADR-016).

Indian banks hand customers their statements as **PDF e-statements** far
more often than CSV. The CSV parser (:mod:`app.services.bank_import`) can
never see those, so a real statement uploaded today falls through the sniff
order all the way to invoice OCR. This module closes that gap.

A statement PDF is a table, and a rasterised-then-OCR'd table is exactly
the kind of mis-read we refuse to put money math behind. So we extract the
PDF's *text layer* with PyMuPDF (no OCR) and reconstruct transactions
structurally:

* Each transaction is a block that begins at a line starting with a
  ``dd/mm/yyyy`` date whose previous line is **not** a date — this collapses
  the txn-date / value-date pair (and works whether the extractor puts them
  on one line or two).
* The only ``…\\.dd`` two-decimal tokens inside a block are the printed
  amount and the running balance (ref numbers, cheque numbers and branch
  codes are integers, so they never collide). The last such token is the
  balance; the one before it (if any) is the printed amount.
* **Direction comes from the running balance, not the column position.**
  ``sign(balance - previous_balance)`` is credit/debit, and the magnitude
  cross-checks the printed amount. This is bank-agnostic — a statement with
  separate Debit/Credit columns and one with a single signed column both
  reduce to the same balance walk — and self-validating: when the printed
  amount disagrees with the balance delta we trust the delta and fall back
  to a narration keyword only when there is no usable delta at all.

Output is the same :class:`~app.services.bank_import.ParsedBankStatement`
the CSV path produces, so the recon leg downstream is unchanged.

Deterministic, no LLM. Money is stringified before :func:`parse_money` so a
binary-float artefact never enters a figure.
"""

from __future__ import annotations

import re
from decimal import Decimal
from itertools import pairwise
from typing import Final

import pymupdf

from app.services.bank_import import (
    BankStatementParseError,
    BankTxn,
    ParsedBankStatement,
    _parse_date,
)
from app.utils.decimal_utils import ZERO, parse_money

__all__ = [
    "looks_like_bank_pdf",
    "parse_bank_pdf",
]

# A two-decimal money token: optional sign, Indian/Western grouping, exactly
# two decimal places. Branch codes / ref numbers / cheque numbers are bare
# integers, so they never match — leaving only the printed amount + balance.
_MONEY_RE: Final[re.Pattern[str]] = re.compile(r"-?\d[\d,]*\.\d{2}(?!\d)")
# A line that *starts* with a dd/mm/yyyy date (txn or value date).
_DATE_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^\s*(\d{2}/\d{2}/\d{4})")
# Strip a leading date off a narration string.
_LEADING_DATE_RE: Final[re.Pattern[str]] = re.compile(r"^\s*\d{2}/\d{2}/\d{4}\s*")
# Opening balance line ("Balance as on 1 Jan 2026 : -4,82,803.51").
_OPENING_BAL_RE: Final[re.Pattern[str]] = re.compile(
    r"balance as on[^:]*:\s*(-?[\d,]+\.\d{2})", re.IGNORECASE
)

# Strong, statement-specific phrases. Invoices and receipts do not carry
# these, so requiring one keeps this parser from stealing a PDF invoice out
# of the OCR path.
_STATEMENT_MARKERS: Final[tuple[str, ...]] = (
    "account statement",
    "statement of account",
    "balance as on",
    "txn date",
    "value date",
    "closing balance",
    "opening balance",
)

# Narration keywords used ONLY when a transaction has no usable balance
# delta (e.g. a missing opening balance on the very first row).
_DEBIT_HINTS: Final[tuple[str, ...]] = (
    "to transfer",
    "to clearing",
    "debit",
    "withdraw",
    "charges",
    "by cheque",
    "atm",
)

# Allow the printed amount to differ from the balance delta by at most this
# (rounding noise) before we distrust it and use the delta instead.
_DELTA_TOLERANCE: Final[Decimal] = Decimal("0.02")
_MIN_TXNS: Final[int] = 2


def _extract_text(raw: bytes) -> str | None:
    """Return the concatenated text layer of a PDF, or ``None`` if not a PDF."""
    if raw[:5] != b"%PDF-":
        return None
    try:
        doc = pymupdf.open(stream=raw, filetype="pdf")
    except Exception:
        return None
    try:
        if doc.is_encrypted:
            return None
        return "\n".join(page.get_text() for page in doc)
    except Exception:
        return None
    finally:
        doc.close()


def _money(token: str) -> Decimal:
    """Parse a two-decimal money token; tolerant, never raises."""
    try:
        return parse_money(token)
    except (ValueError, TypeError):
        return ZERO


def _block_starts(lines: list[str]) -> list[int]:
    """Indices where a transaction block begins.

    A block starts at a date-prefixed line whose previous line is *not*
    date-prefixed. This collapses the consecutive txn-date / value-date
    lines (and the single combined line some extractors emit) to exactly
    one start per transaction.
    """
    starts: list[int] = []
    for i, line in enumerate(lines):
        if _DATE_PREFIX_RE.match(line) and (
            i == 0 or not _DATE_PREFIX_RE.match(lines[i - 1])
        ):
            starts.append(i)
    return starts


def _narration(block: list[str]) -> str:
    """Join a block's non-date, non-money lines into a single narration."""
    parts = [
        line.strip()
        for line in block
        if line.strip()
        and not _MONEY_RE.fullmatch(line.strip())
        and not re.fullmatch(r"\d{2}/\d{2}/\d{4}", line.strip())
    ]
    joined = " ".join(parts)
    joined = _LEADING_DATE_RE.sub("", joined)
    return re.sub(r"\s+", " ", joined).strip()


def _direction_from_narration(narration: str) -> str:
    upper = narration.lower()
    return "debit" if any(h in upper for h in _DEBIT_HINTS) else "credit"


def looks_like_bank_pdf(raw: bytes) -> bool:
    """Cheap sniff: a PDF text layer with a statement marker and dated rows.

    Precise by design — requires a statement-specific phrase plus at least
    two date-prefixed lines — so it won't divert a PDF *invoice* away from
    the OCR pipeline.
    """
    text = _extract_text(raw)
    if text is None:
        return False
    low = text.lower()
    if not any(marker in low for marker in _STATEMENT_MARKERS):
        return False
    dated = sum(1 for line in text.splitlines() if _DATE_PREFIX_RE.match(line))
    return dated >= _MIN_TXNS and bool(_MONEY_RE.search(text))


def parse_bank_pdf(raw: bytes) -> ParsedBankStatement:
    """Parse a bank-statement PDF into normalised transactions.

    Walks the running balance to assign each transaction a direction and a
    self-checked amount. Raises :class:`BankStatementParseError` when the
    bytes are not a parseable statement PDF or yield fewer than two
    transactions.
    """
    text = _extract_text(raw)
    if text is None:
        raise BankStatementParseError("not a readable PDF (encrypted or non-PDF bytes)")

    lines = text.splitlines()
    starts = _block_starts(lines)
    if len(starts) < _MIN_TXNS:
        raise BankStatementParseError(
            "couldn't find dated transaction rows in the PDF text layer"
        )

    opening_match = _OPENING_BAL_RE.search(text)
    prev_balance: Decimal | None = (
        _money(opening_match.group(1)) if opening_match else None
    )

    bounds = [*starts, len(lines)]
    txns: list[BankTxn] = []
    for start, end in pairwise(bounds):
        block = lines[start:end]
        money_tokens = [tok for line in block for tok in _MONEY_RE.findall(line)]
        if not money_tokens:
            continue
        balance = _money(money_tokens[-1])
        printed_amount = _money(money_tokens[-2]) if len(money_tokens) >= 2 else None

        narration = _narration(block)
        txn_date = None
        first_date = _DATE_PREFIX_RE.match(block[0])
        if first_date is not None:
            txn_date = _parse_date(first_date.group(1))
        if txn_date is None:
            prev_balance = balance
            continue

        delta = balance - prev_balance if prev_balance is not None else None
        if delta is not None and delta != ZERO:
            direction = "credit" if delta > ZERO else "debit"
            amount = (
                printed_amount
                if printed_amount is not None
                and abs(abs(delta) - printed_amount) <= _DELTA_TOLERANCE
                else abs(delta)
            )
        else:
            # No usable balance delta (missing opening balance on row 1, or a
            # zero-delta row) — fall back to the printed amount + narration.
            amount = printed_amount if printed_amount is not None else ZERO
            direction = _direction_from_narration(narration)

        prev_balance = balance
        if amount <= ZERO:
            continue
        txns.append(
            BankTxn(
                txn_date=txn_date,
                narration=narration,
                amount=amount,
                direction=direction,
            )
        )

    if len(txns) < _MIN_TXNS:
        raise BankStatementParseError("no transactions parsed from the PDF statement")

    return ParsedBankStatement(transactions=tuple(txns))
