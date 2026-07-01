"""Reconciliation orchestration — the DB + delivery layer over recon.py.

Shared by two callers (kept here so neither the worker nor the supervisor
imports the other):

* the document pipeline, when a CA uploads a GSTR-2B JSON (auto-recon);
* the ``reconcile_itc`` supervisor tool (on-demand re-run from the 2B
  already ingested into ``brain_events``).

The pure matching lives in :mod:`app.services.recon`; this module loads
the two legs, runs the match, delivers the CSV, and records the audit row
plus the ``outcome_units`` that make Track-A pricing real.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.db.models.client import Client
from app.db.models.firm import CaFirm
from app.db.queries.brain_events import BrainEventQuery
from app.db.queries.documents import DocumentQuery
from app.db.queries.outcome_units import OutcomeUnitQuery
from app.db.queries.reconciliation_runs import ReconRunQuery
from app.logging_setup import get_logger
from app.services import telegram
from app.services.gstr2b_import import Gstr2bEntry
from app.services.recon import InvoiceRecord, ReconResult, build_recon_csv, reconcile
from app.utils.decimal_utils import ZERO, parse_money

__all__ = ["ReconDelivery", "period_from_rtnprd", "reconcile_and_deliver"]

_log = get_logger(__name__)

_EXPORTABLE_STATUSES: frozenset[str] = frozenset({"completed", "approved"})
_GSTR2B_SOURCE: str = "gstn_portal"
_GSTR2B_EVENT: str = "gstr2b_entry"


@dataclass(frozen=True)
class ReconDelivery:
    """What the caller gets back: the result + a one-line summary string."""

    result: ReconResult
    summary: str
    run_id: uuid.UUID


def period_from_rtnprd(rtnprd: str) -> tuple[int, int] | None:
    """GST return period ``MMYYYY`` → ``(month, year)``; ``None`` if malformed."""
    s = rtnprd.strip()
    if len(s) != 6 or not s.isdigit():
        return None
    month, year = int(s[:2]), int(s[2:])
    if not (1 <= month <= 12):
        return None
    return month, year


def _parse_iso_date(raw: Any) -> date | None:
    if isinstance(raw, str) and len(raw) >= 10:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None
    return None


def _doc_itc(tax_verdict: dict[str, Any] | None) -> Decimal:
    """Books-side claimed ITC = ``claim_amount`` from the tax verdict."""
    if not isinstance(tax_verdict, dict):
        return ZERO
    raw = tax_verdict.get("claim_amount")
    if raw in (None, ""):
        return ZERO
    try:
        return parse_money(str(raw))
    except (ValueError, TypeError):
        return ZERO


async def _invoice_records(
    *,
    session: Any,
    ca_firm_id: uuid.UUID,
    client_id: uuid.UUID,
    month: int,
    year: int,
) -> list[InvoiceRecord]:
    """Build the books leg from completed/approved documents for the period."""
    docs_q = DocumentQuery(session=session, ca_firm_id=ca_firm_id)
    period_docs = await docs_q.list_by_filing_period(year=year, month=month)
    records: list[InvoiceRecord] = []
    for d in period_docs:
        if d.client_id != client_id:
            continue
        if (d.processing_status or "").lower() not in _EXPORTABLE_STATUSES:
            continue
        ex = d.extraction_data or {}
        vendor = ex.get("vendor") if isinstance(ex.get("vendor"), dict) else {}
        gstin = str((vendor or {}).get("gstin") or "").strip()
        invno = str(ex.get("invoice_number") or "").strip()
        if not gstin or not invno:
            continue
        records.append(
            InvoiceRecord(
                ref=str(d.id)[:8],
                supplier_gstin=gstin,
                invoice_number=invno,
                invoice_date=_parse_iso_date(ex.get("invoice_date")),
                itc_amount=_doc_itc(d.tax_verdict),
            )
        )
    return records


async def entries_from_brain(
    *,
    session: Any,
    ca_firm_id: uuid.UUID,
    client_id: uuid.UUID,
    month: int,
    year: int,
) -> list[Gstr2bEntry]:
    """Load GSTR-2B entries for (client, return period) from brain_events.

    Filters by the ``return_period`` stamped on each payload at ingestion
    (``MMYYYY``) — the GST return period, not the invoice date, which can
    fall in an earlier month for late-filed entries.
    """
    target = f"{month:02d}{year}"
    brain_q = BrainEventQuery(session=session, ca_firm_id=ca_firm_id)
    events = await brain_q.list_filtered(
        client_id=client_id, source=_GSTR2B_SOURCE, event_type=_GSTR2B_EVENT, limit=5000
    )
    out: list[Gstr2bEntry] = []
    for ev in events:
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        if str(payload.get("return_period") or "") != target:
            continue
        try:
            out.append(Gstr2bEntry.from_payload(payload))
        except Exception as exc:  # — one bad row must not sink the recon
            _log.warning("gstr2b_payload_rebuild_failed", error=str(exc))
    return out


async def reconcile_and_deliver(
    *,
    session: Any,
    firm: CaFirm,
    client: Client,
    month: int,
    year: int,
    entries: list[Gstr2bEntry],
    chat_id: int,
    reply_to_message_id: int | None = None,
    record_outcomes: bool = True,
    run_kind: str = "invoice_vs_2b",
) -> ReconDelivery:
    """Run the 2-way recon, deliver the CSV, record audit + outcome units.

    ``entries`` is supplied by the caller — freshly parsed (auto-recon on
    upload) or rebuilt from brain_events (on-demand tool). The books leg
    is always loaded from ``documents`` for the period.

    ``record_outcomes`` gates the Track-A billing hook: the Phase 8d
    period-close assembly re-delivers the authoritative complete-legs report
    but passes ``False`` because the interim on-arrival recon already billed
    the period — re-recording ``outcome_units`` would double-bill. ``run_kind``
    stamps the audit row so those re-runs are distinguishable (and idempotent)
    from the billing recon.
    """
    invoices = await _invoice_records(
        session=session,
        ca_firm_id=firm.id,
        client_id=client.id,
        month=month,
        year=year,
    )
    result = reconcile(invoices=invoices, entries=entries)

    csv_bytes = build_recon_csv(
        result=result, client_name=client.trade_name, month=month, year=year
    )
    report_sha = hashlib.sha256(csv_bytes).hexdigest()
    filename = f"recon_{_slug(client.trade_name)}_{year:04d}-{month:02d}.csv"
    caption = (
        f"<b>ITC reconciliation</b>\n"
        f"Client: {_esc(client.trade_name)}\n"
        f"Period: {month:02d}/{year}\n"
        f"Recoverable ITC: ₹{result.recoverable_itc}"
    )
    await telegram.send_document(
        chat_id=chat_id,
        file_bytes=csv_bytes,
        filename=filename,
        caption_html=caption,
    )

    # Audit row.
    runs_q = ReconRunQuery(session=session, ca_firm_id=firm.id)
    run = await runs_q.record(
        client_id=client.id,
        filing_period_month=month,
        filing_period_year=year,
        matched_count=result.matched_count,
        amount_mismatch_count=result.amount_mismatch_count,
        in_books_not_in_2b_count=result.in_books_not_in_2b_count,
        in_2b_not_in_books_count=result.in_2b_not_in_books_count,
        recoverable_itc=result.recoverable_itc,
        at_risk_itc=result.at_risk_itc,
        report_sha256=report_sha,
        run_by_chat_id=chat_id,
        kind=run_kind,
    )

    # Outcome units — the Track-A pricing hook. Both pending CA approval.
    # Skipped for period-close re-assembly (record_outcomes=False) so the
    # period is billed exactly once, at the interim on-arrival recon.
    if record_outcomes:
        outcomes_q = OutcomeUnitQuery(session=session, ca_firm_id=firm.id)
        await outcomes_q.record(
            kind="reconciled_period",
            quantity=Decimal("1"),
            client_id=client.id,
            confidence=Decimal("1.0"),
            metadata={"month": month, "year": year, "run_id": str(run.id)},
        )
        if result.recoverable_itc > ZERO:
            await outcomes_q.record(
                kind="itc_recovered_inr",
                quantity=result.recoverable_itc,
                client_id=client.id,
                confidence=Decimal("1.0"),
                metadata={
                    "month": month,
                    "year": year,
                    "run_id": str(run.id),
                    "basis": "in_2b_not_in_books",
                },
            )

    await session.commit()

    summary = (
        f"📊 Reconciled {client.trade_name} for {month:02d}/{year}: "
        f"{result.matched_count} matched, "
        f"{result.in_2b_not_in_books_count} recoverable (₹{result.recoverable_itc}), "
        f"{result.in_books_not_in_2b_count} at risk (₹{result.at_risk_itc}), "
        f"{result.amount_mismatch_count} amount mismatches."
    )
    _log.info(
        "reconciliation_completed",
        ca_firm_id=str(firm.id),
        client_id=str(client.id),
        month=month,
        year=year,
        recoverable_itc=str(result.recoverable_itc),
        run_id=str(run.id),
    )
    return ReconDelivery(result=result, summary=summary, run_id=run.id)


def _slug(name: str) -> str:
    cleaned = "".join(c if c.isalnum() else "_" for c in name).strip("_")
    return (cleaned or "client")[:40]


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
