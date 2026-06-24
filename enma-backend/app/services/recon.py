"""Tri-Way ITC reconciliation engine — deterministic 2-way core (ADR-015).

Matches a client's books (the ``documents`` invoice leg) against GSTR-2B
(the available-ITC leg) for a filing period and classifies every line:

* ``matched``              — same invoice on both sides, ITC agrees (±₹1).
* ``amount_mismatch``      — same invoice, ITC differs beyond tolerance.
* ``in_books_not_in_2b``   — claimed in books, not in 2B → ITC at risk
                             (supplier hasn't filed); reverse or chase.
* ``in_2b_not_in_books``   — in 2B, not claimed → **recoverable ITC**
                             (the billable Track-A outcome).

This module is pure and deterministic — no DB, no LLM. The LLM is never
in the matching loop: a hallucinated match is a wrong tax filing. The
caller builds :class:`InvoiceRecord` rows from ``documents`` and passes
GSTR-2B entries; the engine returns a :class:`ReconResult`.

Match key
---------
``(UPPER(gstin), UPPER(invoice_no), ISO-date)`` — the same natural key
the documents table dedupes on. When the exact key misses (invoice-number
format drift between the OCR'd invoice and the portal), a fuzzy fallback
matches on ``(gstin, ISO-date)`` with the ITC amount within tolerance.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from app.services.gstr2b_import import Gstr2bEntry
from app.utils.decimal_utils import ZERO

__all__ = [
    "TOLERANCE",
    "InvoiceRecord",
    "ReconLine",
    "ReconResult",
    "build_recon_csv",
    "reconcile",
]

TOLERANCE: Final[Decimal] = Decimal("1.00")
"""ITC amounts within ₹1 are treated as equal (per-line paisa rounding)."""

_BUCKET_MATCHED: Final[str] = "matched"
_BUCKET_AMOUNT_MISMATCH: Final[str] = "amount_mismatch"
_BUCKET_IN_BOOKS_NOT_2B: Final[str] = "in_books_not_in_2b"
_BUCKET_IN_2B_NOT_BOOKS: Final[str] = "in_2b_not_in_books"


@dataclass(frozen=True)
class InvoiceRecord:
    """A books-side purchase invoice (built from a ``documents`` row)."""

    ref: str
    supplier_gstin: str
    invoice_number: str
    invoice_date: date | None
    itc_amount: Decimal


@dataclass(frozen=True)
class ReconLine:
    """One classified reconciliation line."""

    bucket: str
    supplier_gstin: str
    invoice_number: str
    invoice_date: str | None
    books_itc: Decimal | None
    available_itc: Decimal | None
    delta: Decimal | None
    note: str


@dataclass(frozen=True)
class ReconResult:
    """Aggregate reconciliation outcome for one (client, period)."""

    lines: tuple[ReconLine, ...]
    matched_count: int
    amount_mismatch_count: int
    in_books_not_in_2b_count: int
    in_2b_not_in_books_count: int
    recoverable_itc: Decimal
    at_risk_itc: Decimal
    mismatch_delta_total: Decimal

    @property
    def total_lines(self) -> int:
        return len(self.lines)


# Report ordering: surface the money (recoverable, then mismatches, then
# at-risk) above the already-matched lines.
_BUCKET_ORDER: Final[dict[str, int]] = {
    _BUCKET_IN_2B_NOT_BOOKS: 0,
    _BUCKET_AMOUNT_MISMATCH: 1,
    _BUCKET_IN_BOOKS_NOT_2B: 2,
    _BUCKET_MATCHED: 3,
}


def build_recon_csv(
    *, result: ReconResult, client_name: str, month: int, year: int
) -> bytes:
    """Render a reconciliation result as a utf-8-sig CSV (Excel-friendly ₹).

    Pure — mirrors :mod:`app.services.export`. Lines are ordered money-first
    (recoverable → mismatch → at-risk → matched).
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([f"ITC Reconciliation — {client_name} — {month:02d}/{year}"])
    writer.writerow(
        [
            "Bucket",
            "Supplier GSTIN",
            "Invoice No",
            "Invoice Date",
            "Books ITC (Rs)",
            "GSTR-2B ITC (Rs)",
            "Delta (Rs)",
            "Note",
        ]
    )
    for line in sorted(result.lines, key=lambda x: _BUCKET_ORDER.get(x.bucket, 9)):
        writer.writerow(
            [
                line.bucket,
                line.supplier_gstin,
                line.invoice_number,
                line.invoice_date or "",
                "" if line.books_itc is None else f"{line.books_itc}",
                "" if line.available_itc is None else f"{line.available_itc}",
                "" if line.delta is None else f"{line.delta}",
                line.note,
            ]
        )
    writer.writerow([])
    writer.writerow(["Matched", result.matched_count])
    writer.writerow(["Amount mismatch", result.amount_mismatch_count])
    writer.writerow(["In books, not in 2B (at risk)", result.in_books_not_in_2b_count])
    writer.writerow(["In 2B, not in books (recoverable)", result.in_2b_not_in_books_count])
    writer.writerow(["Recoverable ITC (Rs)", f"{result.recoverable_itc}"])
    writer.writerow(["At-risk ITC (Rs)", f"{result.at_risk_itc}"])
    return buf.getvalue().encode("utf-8-sig")


def _norm_gstin(value: str) -> str:
    return value.strip().upper()


def _norm_invno(value: str) -> str:
    return value.strip().upper()


def _iso(d: date | None) -> str:
    return d.isoformat() if d is not None else ""


def reconcile(  # noqa: PLR0912 — matching/classification is inherently branchy
    *,
    invoices: list[InvoiceRecord],
    entries: list[Gstr2bEntry],
) -> ReconResult:
    """Match books invoices against GSTR-2B entries; classify every line.

    Pure + deterministic. Exact match on (gstin, invoice_no, ISO-date)
    first; unmatched books invoices then try a fuzzy fallback on
    (gstin, ISO-date) with ITC within :data:`TOLERANCE` to absorb
    invoice-number format drift. Whatever is left on each side becomes
    ``in_books_not_in_2b`` / ``in_2b_not_in_books``.
    """
    # Index 2B entries by exact key and by the fuzzy (gstin, date) key.
    exact_2b: dict[tuple[str, str, str], list[Gstr2bEntry]] = defaultdict(list)
    fuzzy_2b: dict[tuple[str, str], list[Gstr2bEntry]] = defaultdict(list)
    for e in entries:
        iso = e.invoice_date.isoformat()
        exact_2b[(_norm_gstin(e.supplier_gstin), _norm_invno(e.invoice_number), iso)].append(e)
        fuzzy_2b[(_norm_gstin(e.supplier_gstin), iso)].append(e)

    used: set[int] = set()  # id() of consumed 2B entries
    lines: list[ReconLine] = []
    matched = mismatch = books_only = 0
    at_risk = ZERO
    mismatch_delta = ZERO

    def _consume(entry: Gstr2bEntry) -> None:
        used.add(id(entry))

    for inv in invoices:
        iso = _iso(inv.invoice_date)
        key = (_norm_gstin(inv.supplier_gstin), _norm_invno(inv.invoice_number), iso)
        candidate: Gstr2bEntry | None = None
        note = ""

        for e in exact_2b.get(key, []):
            if id(e) not in used:
                candidate = e
                break
        if candidate is None:
            # Fuzzy: same supplier + date, ITC within tolerance, not yet used.
            for e in fuzzy_2b.get((_norm_gstin(inv.supplier_gstin), iso), []):
                if id(e) in used:
                    continue
                if abs(e.total_itc - inv.itc_amount) <= TOLERANCE:
                    candidate = e
                    note = "matched on supplier+date+amount (invoice no. differed)"
                    break

        if candidate is None:
            books_only += 1
            at_risk += inv.itc_amount
            lines.append(
                ReconLine(
                    bucket=_BUCKET_IN_BOOKS_NOT_2B,
                    supplier_gstin=inv.supplier_gstin,
                    invoice_number=inv.invoice_number,
                    invoice_date=iso or None,
                    books_itc=inv.itc_amount,
                    available_itc=None,
                    delta=None,
                    note="Claimed in books but not in GSTR-2B — supplier may not have filed.",
                )
            )
            continue

        _consume(candidate)
        delta = inv.itc_amount - candidate.total_itc
        if abs(delta) <= TOLERANCE:
            matched += 1
            lines.append(
                ReconLine(
                    bucket=_BUCKET_MATCHED,
                    supplier_gstin=inv.supplier_gstin,
                    invoice_number=inv.invoice_number,
                    invoice_date=iso or None,
                    books_itc=inv.itc_amount,
                    available_itc=candidate.total_itc,
                    delta=ZERO,
                    note=note or "Matched.",
                )
            )
        else:
            mismatch += 1
            mismatch_delta += abs(delta)
            lines.append(
                ReconLine(
                    bucket=_BUCKET_AMOUNT_MISMATCH,
                    supplier_gstin=inv.supplier_gstin,
                    invoice_number=inv.invoice_number,
                    invoice_date=iso or None,
                    books_itc=inv.itc_amount,
                    available_itc=candidate.total_itc,
                    delta=delta,
                    note=(
                        "ITC differs from GSTR-2B — claim the lower figure "
                        "and investigate the delta."
                    ),
                )
            )

    # Anything in 2B not consumed = available but unclaimed → recoverable.
    recoverable = ZERO
    in_2b_only = 0
    for e in entries:
        if id(e) in used:
            continue
        in_2b_only += 1
        if e.itc_available:
            recoverable += e.total_itc
        lines.append(
            ReconLine(
                bucket=_BUCKET_IN_2B_NOT_BOOKS,
                supplier_gstin=e.supplier_gstin,
                invoice_number=e.invoice_number,
                invoice_date=e.invoice_date.isoformat(),
                books_itc=None,
                available_itc=e.total_itc,
                delta=None,
                note=(
                    "In GSTR-2B but not in your books — likely unclaimed ITC."
                    if e.itc_available
                    else "In GSTR-2B but ITC not available (check eligibility)."
                ),
            )
        )

    return ReconResult(
        lines=tuple(lines),
        matched_count=matched,
        amount_mismatch_count=mismatch,
        in_books_not_in_2b_count=books_only,
        in_2b_not_in_books_count=in_2b_only,
        recoverable_itc=recoverable,
        at_risk_itc=at_risk,
        mismatch_delta_total=mismatch_delta,
    )
