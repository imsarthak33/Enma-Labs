"""GSTR-3B draft assembler (Phase 9 — filing generation). See ADR-020.

Assembles a **draft** GSTR-3B summary for a (client, period) from what the
Brain already holds — it does NOT auto-file. The draft is the content a CA
reviews and locks via the existing ``ENMA APPROVE FILING`` flow (ADR); only an
approved snapshot is eligible for GSP submission (dormant, ADR-020).

Two sides, from two reliable sources:

* **Outward tax liability (3.1(a))** — summed from ``voucher_sales``
  brain_events in the period. Tax is bucketed (igst/cgst/sgst/cess) by ledger
  name; the taxable value is the party (invoice) total net of that tax.
* **ITC available (Table 4)** — taken from the period's most recent
  reconciliation run (the recoverable ITC the Tri-Way recon computed).

Everything is :class:`Decimal` (never float — money). Figures derived by
heuristic (tax-ledger name matching, the ITC proxy) are surfaced in
``notes`` so the CA verifies rather than trusts blindly — this is a draft.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.db.queries.brain_events import BrainEventQuery
from app.db.queries.reconciliation_runs import ReconRunQuery
from app.logging_setup import get_logger
from app.utils.decimal_utils import ZERO, parse_money

__all__ = ["Gstr3bDraft", "TaxAmounts", "build_gstr3b_draft"]

_log = get_logger(__name__)

_SALES_EVENT: str = "voucher_sales"
_TALLY_SOURCE: str = "tally"

# Substring → tax bucket. Tally output-tax ledgers are conventionally named
# ("Output IGST", "CGST", "SGST @9%", "Cess"). Order matters: check the
# combined/integrated tax first so "igst" isn't shadowed by "gst".
_TAX_MARKERS: tuple[tuple[str, str], ...] = (
    ("igst", "igst"),
    ("cess", "cess"),
    ("cgst", "cgst"),
    ("sgst", "sgst"),
    ("utgst", "sgst"),  # union-territory tax rides the sgst bucket in 3.1(a)
)


@dataclass(frozen=True)
class TaxAmounts:
    """A taxable value plus its four GST components."""

    taxable_value: Decimal = ZERO
    igst: Decimal = ZERO
    cgst: Decimal = ZERO
    sgst: Decimal = ZERO
    cess: Decimal = ZERO

    @property
    def total_tax(self) -> Decimal:
        return self.igst + self.cgst + self.sgst + self.cess


@dataclass(frozen=True)
class Gstr3bDraft:
    """A DRAFT GSTR-3B summary for one client + period. Not auto-filed."""

    client_id: uuid.UUID
    month: int
    year: int
    outward: TaxAmounts
    itc_available: Decimal
    net_tax_payable: Decimal
    sales_voucher_count: int
    is_draft: bool = True
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_payload(self) -> dict[str, Any]:
        """JSON-safe snapshot (money as strings) for the approval record."""
        return {
            "return_type": "GSTR3B",
            "is_draft": self.is_draft,
            "client_id": str(self.client_id),
            "filing_period": f"{self.month:02d}{self.year}",
            "outward_supplies_3_1_a": {
                "taxable_value": str(self.outward.taxable_value),
                "igst": str(self.outward.igst),
                "cgst": str(self.outward.cgst),
                "sgst": str(self.outward.sgst),
                "cess": str(self.outward.cess),
                "total_tax": str(self.outward.total_tax),
            },
            "itc_available_table_4": str(self.itc_available),
            "net_tax_payable": str(self.net_tax_payable),
            "sales_voucher_count": self.sales_voucher_count,
            "notes": list(self.notes),
        }


def _classify_tax_ledger(ledger_name: str) -> str | None:
    """Return the tax bucket a ledger contributes to, or ``None`` (non-tax)."""
    low = ledger_name.lower()
    for marker, bucket in _TAX_MARKERS:
        if marker in low:
            return bucket
    return None


def _period_bounds(month: int, year: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=UTC)
    end = (
        datetime(year + 1, 1, 1, tzinfo=UTC)
        if month == 12
        else datetime(year, month + 1, 1, tzinfo=UTC)
    )
    return start, end


def _sum_sales(events: list[Any], *, month: int, year: int) -> tuple[TaxAmounts, int]:
    """Aggregate outward tax + taxable value from sales vouchers in the period."""
    start, end = _period_bounds(month, year)
    igst = cgst = sgst = cess = taxable = ZERO
    counted = 0
    for ev in events:
        occurred = getattr(ev, "occurred_at", None)
        if occurred is not None and not (start <= occurred < end):
            continue
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        entries = payload.get("ledger_entries")
        if not isinstance(entries, list):
            continue
        counted += 1
        v_tax = ZERO
        party_total = ZERO
        for e in entries:
            if not isinstance(e, dict):
                continue
            try:
                amount = parse_money(str(e.get("amount") or "0"))
            except (ValueError, TypeError):
                continue
            magnitude = amount if amount >= ZERO else -amount
            if e.get("is_party"):
                party_total += magnitude
                continue
            bucket = _classify_tax_ledger(str(e.get("ledger_name") or ""))
            if bucket == "igst":
                igst += magnitude
            elif bucket == "cgst":
                cgst += magnitude
            elif bucket == "sgst":
                sgst += magnitude
            elif bucket == "cess":
                cess += magnitude
            v_tax += magnitude if bucket else ZERO
        # Taxable value = invoice (party) total net of its tax. When no party
        # ledger is present, fall back to the non-tax ledger sum is skipped —
        # the party total is Tally's authoritative invoice value.
        taxable += party_total - v_tax if party_total > ZERO else ZERO
    return (
        TaxAmounts(taxable_value=taxable, igst=igst, cgst=cgst, sgst=sgst, cess=cess),
        counted,
    )


async def build_gstr3b_draft(
    *,
    session: Any,
    ca_firm_id: uuid.UUID,
    client_id: uuid.UUID,
    month: int,
    year: int,
) -> Gstr3bDraft:
    """Assemble the DRAFT GSTR-3B for a client + period. Read-only, firm-scoped."""
    brain_q = BrainEventQuery(session=session, ca_firm_id=ca_firm_id)
    sales = list(
        await brain_q.list_filtered(
            client_id=client_id,
            source=_TALLY_SOURCE,
            event_type=_SALES_EVENT,
            limit=10000,
        )
    )
    outward, count = _sum_sales(sales, month=month, year=year)

    runs_q = ReconRunQuery(session=session, ca_firm_id=ca_firm_id)
    recent = list(await runs_q.list_recent(client_id=client_id, limit=25))
    itc = ZERO
    period_run = next(
        (
            r
            for r in recent
            if r.filing_period_month == month and r.filing_period_year == year
        ),
        None,
    )
    notes: list[str] = [
        "DRAFT — verify every figure before filing.",
        "Outward tax bucketed from Tally sales-voucher ledger names; "
        "confirm ledger naming matches your chart of accounts.",
    ]
    if period_run is not None:
        itc = period_run.recoverable_itc or ZERO
        notes.append(
            "ITC shown is the recoverable ITC from the latest reconciliation; "
            "add already-matched ITC for the full Table-4 eligible figure."
        )
    else:
        notes.append(
            "No reconciliation found for this period — ITC is zero here; "
            "run the recon (or upload GSTR-2B) first."
        )

    net = outward.total_tax - itc
    if net < ZERO:
        net = ZERO
        notes.append("Net liability floored at 0 (ITC exceeds outward tax).")

    _log.info(
        "gstr3b_draft_built",
        ca_firm_id=str(ca_firm_id),
        client_id=str(client_id),
        month=month,
        year=year,
        outward_tax=str(outward.total_tax),
        itc=str(itc),
        sales_vouchers=count,
    )
    return Gstr3bDraft(
        client_id=client_id,
        month=month,
        year=year,
        outward=outward,
        itc_available=itc,
        net_tax_payable=net,
        sales_voucher_count=count,
        notes=tuple(notes),
    )
