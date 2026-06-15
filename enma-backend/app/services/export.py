"""Client ledger CSV exporter — human-readable summary for CAs.

The CA's chief end-of-month workflow is: open the firm's books, paste
this CSV into a spreadsheet, eyeball the totals, reconcile with the
GSTR-2B portal. This module emits exactly that file for a (client,
period) tuple — one row per document plus a summary row.

Where Tally XML (:mod:`app.services.tally`) is the *structured-import*
view of the same data, the CSV is the *audit-trail* view: lossy on
internal IDs, friendly on labels.

Format choices
--------------
* **utf-8-sig** (BOM) so Excel renders the ₹ symbol correctly.
* **decimal.Decimal** for every money cell — never float.
* **IST timestamps** for the "Logged at" column (the CA's wall clock).
* **Reconciler-canonical totals** — we re-run ``reconcile_extraction``
  on every document so the CSV reflects the latest math, not whatever
  the LLM hallucinated at ingest time.
* **Sale / Purchase classification** by GSTIN match: if the client's
  GSTIN appears as the buyer, it's a PURCHASE; if as the vendor, it's
  a SALE; otherwise we default to PURCHASE (the dominant case today —
  every document type we ingest is an inbound invoice).
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.document import Document
from app.db.queries.clients import ClientQuery
from app.db.queries.documents import DocumentQuery
from app.tax.reconciler import reconcile_extraction
from app.utils.date_utils import IST, parse_iso_date
from app.utils.decimal_utils import ZERO

__all__ = [
    "LEDGER_HEADERS",
    "generate_client_ledger_csv",
]


_EXPORTABLE_STATUSES: Final[frozenset[str]] = frozenset(
    {"completed", "approved", "reconciled"}
)

LEDGER_HEADERS: Final[tuple[str, ...]] = (
    "Invoice Date",
    "Invoice No",
    "Vendor Name",
    "Buyer Name",
    "Taxable Value (₹)",
    "CGST (₹)",
    "SGST (₹)",
    "IGST (₹)",
    "Grand Total (₹)",
    "ITC Status",
    "Document Type",
    "Logged At",
)


_IST_TIMESTAMP_FORMAT: Final[str] = "%d-%b-%Y %I:%M %p IST"


# ---------------------------------------------------------------------------
# Per-row composition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LedgerRow:
    """One CSV line plus the numeric totals it contributed."""

    cells: tuple[str, ...]
    taxable: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    grand_total: Decimal


def _format_money(value: Decimal) -> str:
    """Two-decimal rendering matching Tally + Indian CA convention."""
    return f"{value:.2f}"


def _format_logged_at(created_at: datetime) -> str:
    """Render a UTC timestamp as IST wall clock (CA-readable)."""
    return created_at.astimezone(IST).strftime(_IST_TIMESTAMP_FORMAT)


def _derive_itc_status(tax_verdict: dict[str, Any] | None) -> str:
    """Pick a single CA-readable ITC status from the verdict.

    Tie-breaker: ELIGIBLE > BLOCKED > DEFERRED > RCM > PENDING. RCM is
    surfaced as a suffix when it co-exists with another bucket so an
    auditor can spot the reverse-charge liability at a glance.
    """
    if not isinstance(tax_verdict, dict):
        return "PENDING"

    def _amt(key: str) -> Decimal:
        try:
            return Decimal(str(tax_verdict.get(key) or "0"))
        except (ValueError, ArithmeticError):
            return ZERO

    claim = _amt("claim_amount")
    block = _amt("block_amount")
    defer = _amt("defer_amount")
    rcm = _amt("rcm_liability")

    if claim > ZERO and claim >= block and claim >= defer:
        status = "ELIGIBLE"
    elif block > ZERO and block >= defer:
        status = "BLOCKED"
    elif defer > ZERO:
        status = "DEFERRED"
    elif rcm > ZERO:
        status = "RCM"
    else:
        status = "PENDING"

    if rcm > ZERO and status != "RCM":
        status = f"{status} + RCM"
    return status


def _classify_sale_or_purchase(
    *,
    extraction: dict[str, Any],
    client_gstin: str | None,
) -> str:
    """Return ``"SALE"`` or ``"PURCHASE"`` for the client's perspective.

    Match on canonical (uppercase, stripped) GSTIN. Default to PURCHASE
    because every document type we currently ingest is an inbound
    invoice the client received.
    """
    if client_gstin is None:
        return "PURCHASE"
    needle = client_gstin.strip().upper()

    def _norm(value: object) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
        return None

    vendor_gstin = _norm((extraction.get("vendor") or {}).get("gstin"))
    buyer_gstin = _norm((extraction.get("buyer") or {}).get("gstin"))

    if vendor_gstin == needle:
        return "SALE"
    if buyer_gstin == needle:
        return "PURCHASE"
    return "PURCHASE"


def _compose_row(
    *,
    doc: Document,
    client_gstin: str | None,
) -> _LedgerRow:
    extraction = doc.extraction_data or {}
    reconciled = reconcile_extraction(extraction)

    inv_date = parse_iso_date(extraction.get("invoice_date"))
    inv_date_str = inv_date.strftime("%d-%b-%Y") if inv_date else ""
    inv_no = str(extraction.get("invoice_number") or "").strip()
    vendor = str((extraction.get("vendor") or {}).get("name") or "").strip()
    buyer = str((extraction.get("buyer") or {}).get("name") or "").strip()

    itc_status = _derive_itc_status(doc.tax_verdict)
    sale_or_purchase = _classify_sale_or_purchase(
        extraction=extraction, client_gstin=client_gstin
    )

    cells: tuple[str, ...] = (
        inv_date_str,
        inv_no,
        vendor,
        buyer,
        _format_money(reconciled.taxable),
        _format_money(reconciled.cgst_amount),
        _format_money(reconciled.sgst_amount),
        _format_money(reconciled.igst_amount),
        _format_money(reconciled.grand_total),
        itc_status,
        sale_or_purchase,
        _format_logged_at(doc.created_at),
    )
    return _LedgerRow(
        cells=cells,
        taxable=reconciled.taxable,
        cgst=reconciled.cgst_amount,
        sgst=reconciled.sgst_amount,
        igst=reconciled.igst_amount,
        grand_total=reconciled.grand_total,
    )


# ---------------------------------------------------------------------------
# Composer — pure-functional core
# ---------------------------------------------------------------------------


def _compose_ledger_csv(
    *,
    documents: Sequence[Document],
    client_gstin: str | None,
) -> bytes:
    """Render the CSV bytes. Pure function; no DB or network."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(LEDGER_HEADERS)

    total_taxable = ZERO
    total_cgst = ZERO
    total_sgst = ZERO
    total_igst = ZERO
    total_grand = ZERO

    for doc in documents:
        row = _compose_row(doc=doc, client_gstin=client_gstin)
        writer.writerow(row.cells)
        total_taxable += row.taxable
        total_cgst += row.cgst
        total_sgst += row.sgst
        total_igst += row.igst
        total_grand += row.grand_total

    # Summary row — totals across all included documents.
    writer.writerow(
        (
            "SUMMARY",
            f"{len(documents)} invoice(s)",
            "",
            "",
            _format_money(total_taxable),
            _format_money(total_cgst),
            _format_money(total_sgst),
            _format_money(total_igst),
            _format_money(total_grand),
            "",
            "",
            "",
        )
    )

    # utf-8-sig prepends the BOM so Excel renders ₹ correctly.
    return buf.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------------------
# DB-aware entry point
# ---------------------------------------------------------------------------


async def generate_client_ledger_csv(
    *,
    session: AsyncSession,
    ca_firm_id: uuid.UUID,
    client_id: uuid.UUID,
    month: int | None = None,
    year: int | None = None,
) -> bytes:
    """Fetch documents for ``(client, period)`` and render the ledger CSV.

    Tenant isolation is enforced by ``BaseQuery`` — every read carries
    ``ca_firm_id``. Documents not in ``_EXPORTABLE_STATUSES``
    (``completed`` / ``approved`` / ``reconciled``) are skipped so a
    half-processed batch doesn't pollute the audit trail.

    When both ``month`` and ``year`` are supplied, only documents in
    that filing period are included; when both are ``None``, every
    completed document for the client is included. Providing only one
    of the two raises ``ValueError`` so the supervisor tool can surface
    a clear error to the CA.
    """
    if (month is None) != (year is None):
        raise ValueError("month and year must both be set, or both be None")

    docs_q = DocumentQuery(session=session, ca_firm_id=ca_firm_id)
    if month is not None and year is not None:
        period_docs = await docs_q.list_by_filing_period(year=year, month=month)
        scoped = [d for d in period_docs if d.client_id == client_id]
    else:
        scoped = list(await docs_q.list_by_client(client_id=client_id))

    eligible = [
        d for d in scoped
        if (d.processing_status or "").lower() in _EXPORTABLE_STATUSES
    ]
    # Deterministic ordering for byte-stable output: by invoice date
    # when available, falling back to created_at.
    eligible.sort(
        key=lambda d: (
            parse_iso_date((d.extraction_data or {}).get("invoice_date"))
            or d.created_at.date(),
            d.created_at,
        )
    )

    clients_q = ClientQuery(session=session, ca_firm_id=ca_firm_id)
    client = await clients_q.get_by_id(client_id)
    client_gstin = client.gstin if client is not None else None

    return _compose_ledger_csv(documents=eligible, client_gstin=client_gstin)
