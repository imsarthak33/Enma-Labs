"""Bank-reconciliation orchestration — DB + delivery over bank_recon.py.

Shared by the worker (auto bank-recon on statement upload) and the
``reconcile_bank`` supervisor tool. The pure matching lives in
:mod:`app.services.bank_recon`; this loads the client's purchases, runs
the 180-day match against the bank debits, and delivers the CSV.

Bank recon is client-wide, not period-bound (the 180-day window spans
filing periods), so it does not write a ``reconciliation_runs`` row — that
table is GSTR-2B-period-shaped. A dedicated bank-run audit is a follow-up.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.db.models.client import Client
from app.db.models.firm import CaFirm
from app.db.queries.brain_events import BrainEventQuery
from app.db.queries.documents import DocumentQuery
from app.db.queries.outcome_units import OutcomeUnitQuery
from app.logging_setup import get_logger
from app.services import telegram
from app.services.bank_import import BankTxn, ParsedBankStatement
from app.services.bank_recon import (
    PurchaseForPayment,
    build_bank_recon_csv,
    reconcile_payments,
)
from app.utils.decimal_utils import ZERO, parse_money

__all__ = [
    "BankReconDelivery",
    "bank_reconcile_and_deliver",
    "statement_from_payload",
    "statement_to_payload",
    "txns_from_brain",
    "txns_to_brain_rows",
]

_log = get_logger(__name__)

_EXPORTABLE_STATUSES: frozenset[str] = frozenset({"completed", "approved"})


@dataclass(frozen=True)
class BankReconDelivery:
    summary: str


def _parse_iso_date(raw: Any) -> date | None:
    if isinstance(raw, str) and len(raw) >= 10:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None
    return None


def _money(raw: Any) -> Decimal:
    if raw in (None, ""):
        return ZERO
    try:
        return parse_money(str(raw))
    except (ValueError, TypeError):
        return ZERO


def _grand_total(extraction: dict[str, Any]) -> Decimal:
    """Pull the payable (invoice grand total) from an extraction."""
    totals_obj = extraction.get("totals")
    totals: dict[str, Any] = totals_obj if isinstance(totals_obj, dict) else {}
    for key in ("grand_total", "invoice_value", "total_amount", "total"):
        val = totals.get(key)
        if val not in (None, ""):
            amt = _money(val)
            if amt > ZERO:
                return amt
    return ZERO


def _doc_itc(tax_verdict: dict[str, Any] | None) -> Decimal:
    if not isinstance(tax_verdict, dict):
        return ZERO
    return _money(tax_verdict.get("claim_amount"))


async def _purchases_for_payment(
    *, session: Any, ca_firm_id: uuid.UUID, client_id: uuid.UUID
) -> list[PurchaseForPayment]:
    """All completed/approved purchase invoices for a client (period-agnostic)."""
    docs_q = DocumentQuery(session=session, ca_firm_id=ca_firm_id)
    all_docs = await docs_q.list_all()
    out: list[PurchaseForPayment] = []
    for d in all_docs:
        if d.client_id != client_id:
            continue
        if (d.processing_status or "").lower() not in _EXPORTABLE_STATUSES:
            continue
        ex = d.extraction_data or {}
        vendor = ex.get("vendor") if isinstance(ex.get("vendor"), dict) else {}
        vendor_name = str((vendor or {}).get("name") or "").strip()
        grand_total = _grand_total(ex)
        if not vendor_name or grand_total <= ZERO:
            continue
        out.append(
            PurchaseForPayment(
                ref=str(d.id)[:8],
                vendor_name=vendor_name,
                invoice_date=_parse_iso_date(ex.get("invoice_date")),
                grand_total=grand_total,
                itc_amount=_doc_itc(d.tax_verdict),
            )
        )
    return out


def statement_to_payload(statement: ParsedBankStatement) -> list[dict[str, Any]]:
    """Serialise a parsed statement to JSON rows (for a pending-assignment hold).

    Lets an un-routed bank-statement upload park its already-parsed
    transactions on the pending row, so a later "which client?" reply can
    resume ingestion without re-downloading or re-parsing the file.
    """
    return [
        {
            "date": t.txn_date.isoformat(),
            "narration": t.narration,
            "amount": str(t.amount),
            "direction": t.direction,
        }
        for t in statement.transactions
    ]


def statement_from_payload(rows: list[dict[str, Any]]) -> ParsedBankStatement:
    """Rebuild a parsed statement from :func:`statement_to_payload` JSON rows."""
    txns: list[BankTxn] = []
    for r in rows:
        d = _parse_iso_date(r.get("date"))
        if d is None:
            continue
        txns.append(
            BankTxn(
                txn_date=d,
                narration=str(r.get("narration") or ""),
                amount=_money(r.get("amount")),
                direction=str(r.get("direction") or "debit"),
            )
        )
    return ParsedBankStatement(transactions=tuple(txns))


def txns_to_brain_rows(
    *, transactions: list[BankTxn], client_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Map bank transactions to brain_event rows (source='bank')."""
    rows: list[dict[str, Any]] = []
    for t in transactions:
        key = hashlib.sha256(
            f"{t.txn_date.isoformat()}|{t.narration}|{t.amount}|{t.direction}".encode()
        ).hexdigest()
        rows.append(
            {
                "source": "bank",
                "event_type": "bank_txn",
                "dedup_key": key,
                "occurred_at": datetime(
                    t.txn_date.year, t.txn_date.month, t.txn_date.day, tzinfo=UTC
                ),
                "payload": {
                    "date": t.txn_date.isoformat(),
                    "narration": t.narration,
                    "amount": str(t.amount),
                    "direction": t.direction,
                },
                "client_id": client_id,
            }
        )
    return rows


async def txns_from_brain(
    *, session: Any, ca_firm_id: uuid.UUID, client_id: uuid.UUID
) -> list[BankTxn]:
    """Rebuild bank transactions for a client from brain_events (on-demand)."""
    brain_q = BrainEventQuery(session=session, ca_firm_id=ca_firm_id)
    events = await brain_q.list_filtered(
        client_id=client_id, source="bank", event_type="bank_txn", limit=10000
    )
    out: list[BankTxn] = []
    for ev in events:
        p = ev.payload if isinstance(ev.payload, dict) else {}
        d = _parse_iso_date(p.get("date"))
        if d is None:
            continue
        out.append(
            BankTxn(
                txn_date=d,
                narration=str(p.get("narration") or ""),
                amount=_money(p.get("amount")),
                direction=str(p.get("direction") or "debit"),
            )
        )
    return out


async def bank_reconcile_and_deliver(
    *,
    session: Any,
    firm: CaFirm,
    client: Client,
    transactions: list[BankTxn],
    chat_id: int,
    reply_to_message_id: int | None = None,
) -> BankReconDelivery:
    """Run the 180-day bank recon for a client and deliver the CSV."""
    purchases = await _purchases_for_payment(
        session=session, ca_firm_id=firm.id, client_id=client.id
    )
    debits = [t for t in transactions if t.direction == "debit"]
    result = reconcile_payments(
        purchases=purchases, debits=debits, as_of=datetime.now(UTC).date()
    )

    # Outcome unit — the bank leg's Track-A pricing hook (TA-1). The
    # 180-day reversal-risk rupee figure is the billable signal this recon
    # surfaces; without it the bank leg produced no trace the outcome meter
    # could see. Confidence is < 1.0 because bank matching is advisory (the
    # narration carries no invoice number — "no payment found" is not proof).
    if result.reversal_risk_itc > ZERO:
        outcomes_q = OutcomeUnitQuery(session=session, ca_firm_id=firm.id)
        await outcomes_q.record(
            kind="itc_reversal_risk_inr",
            quantity=result.reversal_risk_itc,
            client_id=client.id,
            confidence=Decimal("0.7"),
            metadata={
                "unpaid_over_180_count": result.unpaid_over_180_count,
                "basis": "no_payment_within_180_days",
            },
        )
        await session.commit()

    csv_bytes = build_bank_recon_csv(result=result, client_name=client.trade_name)
    filename = f"bank_recon_{_slug(client.trade_name)}.csv"
    caption = (
        f"<b>Bank / 180-day ITC reconciliation</b>\n"
        f"Client: {_esc(client.trade_name)}\n"
        f"ITC at reversal risk: ₹{result.reversal_risk_itc}"
    )
    await telegram.send_document(
        chat_id=chat_id,
        file_bytes=csv_bytes,
        filename=filename,
        caption_html=caption,
    )

    _log.info(
        "bank_reconciliation_completed",
        ca_firm_id=str(firm.id),
        client_id=str(client.id),
        paid=result.paid_count,
        unpaid_over_180=result.unpaid_over_180_count,
        reversal_risk_itc=str(result.reversal_risk_itc),
    )
    summary = (
        f"📊 Bank recon for {client.trade_name}: {result.paid_count} paid, "
        f"{result.unpaid_over_180_count} over 180 days "
        f"(₹{result.reversal_risk_itc} ITC to reverse), "
        f"{result.unpaid_within_180_count} still within window."
    )
    return BankReconDelivery(summary=summary)


def _slug(name: str) -> str:
    cleaned = "".join(c if c.isalnum() else "_" for c in name).strip("_")
    return (cleaned or "client")[:40]


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
