"""Bank-statement CSV parser — the payment leg of Tri-Way recon (ADR-016).

Unlike GSTR-2B (one published JSON schema), bank statements have no
standard format — every bank's CSV column names and preamble differ, and
rows carry no GSTIN/invoice number. So this parser is deliberately
*header-tolerant*: it scans for the header row (banks prepend account-info
lines), then maps columns by fuzzy keyword against the common Indian-bank
variants (Txn Date / Narration / Withdrawal / Deposit, etc.).

Output is a list of normalised :class:`BankTxn` (date, narration, amount,
direction). The matcher (``bank_recon``) only uses the *debit* rows —
outbound payments to suppliers — but we parse both directions.

Deterministic, no LLM. Money is stringified before :func:`parse_money`
so a binary-float artefact never enters a figure.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Final

from app.utils.decimal_utils import ZERO, parse_money

__all__ = [
    "BankStatementParseError",
    "BankTxn",
    "ParsedBankStatement",
    "looks_like_bank_csv",
    "parse_bank_statement",
]


class BankStatementParseError(ValueError):
    """Raised when bytes are not a parseable bank-statement CSV."""


@dataclass(frozen=True)
class BankTxn:
    """One normalised bank transaction."""

    txn_date: date
    narration: str
    amount: Decimal
    direction: str  # 'debit' (money out) | 'credit' (money in)


@dataclass(frozen=True)
class ParsedBankStatement:
    """All transactions parsed from one statement."""

    transactions: tuple[BankTxn, ...]


# Header keyword sets — lower-cased substring match against header cells.
_DATE_KEYS: Final[tuple[str, ...]] = (
    "txn date", "value date", "transaction date", "tran date",
    "posting date", "date",
)
_NARRATION_KEYS: Final[tuple[str, ...]] = (
    "narration", "particulars", "description", "remarks", "details", "transaction remarks",
)
_DEBIT_KEYS: Final[tuple[str, ...]] = ("withdrawal", "debit", "dr amount", "withdrawal amt")
_CREDIT_KEYS: Final[tuple[str, ...]] = ("deposit", "credit", "cr amount", "deposit amt")
_AMOUNT_KEYS: Final[tuple[str, ...]] = ("amount", "txn amount")
_DRCR_KEYS: Final[tuple[str, ...]] = ("dr / cr", "dr/cr", "type", "indicator", "drcr")

_DATE_FORMATS: Final[tuple[str, ...]] = (
    "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y", "%d %b %Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y",
)


def _match_col(header: list[str], keys: tuple[str, ...]) -> int | None:
    """Return the index of the first header cell containing any keyword.

    Longer keywords are tried first so 'withdrawal amt' wins over a bare
    'amount' column when both are present.
    """
    norm = [c.strip().lower() for c in header]
    for key in sorted(keys, key=len, reverse=True):
        for i, cell in enumerate(norm):
            if key in cell:
                return i
    return None


def _cell(row: list[str], col: int | None) -> str:
    """Safe cell read by column index; '' when the index is absent/out of range."""
    if col is None or col >= len(row):
        return ""
    return row[col]


def _parse_date(raw: str) -> date | None:
    text = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _money(raw: str) -> Decimal:
    """Parse a statement amount cell; '', '-', 'Cr'/'Dr' suffixes → tolerant."""
    cleaned = raw.strip()
    if not cleaned or cleaned in {"-", "—"}:
        return ZERO
    try:
        return parse_money(cleaned)
    except (ValueError, TypeError):
        return ZERO


def _rows(raw: bytes) -> list[list[str]]:
    text = raw.decode("utf-8-sig", errors="replace")
    return [row for row in csv.reader(io.StringIO(text)) if any(c.strip() for c in row)]


def _find_header(rows: list[list[str]]) -> int | None:
    """Locate the header row: has a date column AND a debit/credit/amount column."""
    for i, row in enumerate(rows[:25]):  # banks rarely prepend >25 preamble lines
        if _match_col(row, _DATE_KEYS) is None:
            continue
        if (
            _match_col(row, _DEBIT_KEYS) is not None
            or _match_col(row, _CREDIT_KEYS) is not None
            or _match_col(row, _AMOUNT_KEYS) is not None
        ):
            return i
    return None


def looks_like_bank_csv(raw: bytes) -> bool:
    """Cheap sniff: a CSV whose header has a date + debit/credit/amount column."""
    head = raw[:8192].lstrip()
    if head[:1] in (b"{", b"<"):  # JSON / XML — not a bank CSV
        return False
    try:
        rows = _rows(raw[:8192])
    except Exception:
        return False
    return _find_header(rows) is not None


def parse_bank_statement(raw: bytes) -> ParsedBankStatement:
    """Parse a bank-statement CSV into normalised transactions.

    Raises :class:`BankStatementParseError` when no recognisable header is
    found. Rows with an unparseable date or zero amount are skipped.
    """
    rows = _rows(raw)
    header_idx = _find_header(rows)
    if header_idx is None:
        raise BankStatementParseError(
            "couldn't find a bank-statement header (need a Date column and a "
            "Withdrawal/Deposit or Debit/Credit or Amount column)"
        )
    header = rows[header_idx]
    date_col = _match_col(header, _DATE_KEYS)
    narr_col = _match_col(header, _NARRATION_KEYS)
    debit_col = _match_col(header, _DEBIT_KEYS)
    credit_col = _match_col(header, _CREDIT_KEYS)
    amount_col = _match_col(header, _AMOUNT_KEYS)
    drcr_col = _match_col(header, _DRCR_KEYS)

    txns: list[BankTxn] = []
    for row in rows[header_idx + 1 :]:
        if date_col is None or date_col >= len(row):
            continue
        txn_date = _parse_date(_cell(row, date_col))
        if txn_date is None:
            continue
        narration = _cell(row, narr_col).strip()

        amount = ZERO
        direction = ""
        if debit_col is not None or credit_col is not None:
            debit = _money(_cell(row, debit_col))
            credit = _money(_cell(row, credit_col))
            if debit > ZERO:
                amount, direction = debit, "debit"
            elif credit > ZERO:
                amount, direction = credit, "credit"
        elif amount_col is not None:
            amount = _money(_cell(row, amount_col))
            # Single amount column: use a Dr/Cr indicator if present.
            indicator = _cell(row, drcr_col).strip().lower()
            direction = "credit" if indicator.startswith("c") else "debit"

        if amount <= ZERO or not direction:
            continue
        txns.append(
            BankTxn(txn_date=txn_date, narration=narration, amount=amount, direction=direction)
        )

    if not txns:
        raise BankStatementParseError("no transactions parsed from the statement")

    return ParsedBankStatement(transactions=tuple(txns))
