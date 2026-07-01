"""Completeness ledger (Phase 8b) — "does this client have everything for March?"

The Tri-Way ITC reconciliation needs three data *legs* for a client and
filing period. This module answers, per ``(client, month, year)``, which
legs have arrived and which are still missing — the completeness view the
CA (and, later, the proactive chase cron) reads before a return is filed.

The legs mirror exactly what the recon consumes, so "complete" here means
"the recon has all its inputs" — no separate notion of required-ness to
drift out of sync:

* ``books``   — exportable purchase documents in the period
  (:meth:`DocumentQuery.list_by_filing_period`, ``completed``/``approved``).
* ``gstr2b``  — GSTR-2B entries in ``brain_events`` (``source='gstn_portal'``)
  whose payload ``return_period`` matches ``MMYYYY``.
* ``bank``    — bank transactions in ``brain_events`` (``source='bank'``,
  ``event_type='bank_txn'``). Bank statements aren't return-period stamped,
  so — like the 180-day recon — presence of *any* bank txn for the client
  is the signal that the payment leg has arrived.

Read-only and firm-scoped via the existing BaseQuery classes. No new
tables: completeness is derived from what's already in the Brain + ledger.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.db.queries.brain_events import BrainEventQuery
from app.db.queries.clients import ClientQuery
from app.db.queries.documents import DocumentQuery

__all__ = [
    "LEG_BANK",
    "LEG_BOOKS",
    "LEG_GSTR2B",
    "ClientCompleteness",
    "LegStatus",
    "assess_completeness",
    "assess_firm_completeness",
]

# Leg identifiers — stable strings so callers (supervisor tool, chase cron)
# and tests refer to legs by name, not position.
LEG_BOOKS: str = "books"
LEG_GSTR2B: str = "gstr2b"
LEG_BANK: str = "bank"

# Mirror the recon's own constants so the two never diverge.
_EXPORTABLE_STATUSES: frozenset[str] = frozenset({"completed", "approved"})
_GSTR2B_SOURCE: str = "gstn_portal"
_GSTR2B_EVENT: str = "gstr2b_entry"
_BANK_SOURCE: str = "bank"
_BANK_EVENT: str = "bank_txn"

# Human labels for rendering — kept out of the data so callers format freely.
LEG_LABELS: dict[str, str] = {
    LEG_BOOKS: "Purchase invoices / books",
    LEG_GSTR2B: "GSTR-2B (ITC)",
    LEG_BANK: "Bank statement",
}


@dataclass(frozen=True)
class LegStatus:
    """Presence of one data leg for a (client, period)."""

    leg: str
    present: bool
    count: int


@dataclass(frozen=True)
class ClientCompleteness:
    """The three-leg completeness view for one client and filing period."""

    client_id: uuid.UUID
    trade_name: str
    month: int
    year: int
    legs: tuple[LegStatus, ...]

    @property
    def is_complete(self) -> bool:
        """True when every leg the recon needs has arrived."""
        return all(leg.present for leg in self.legs)

    @property
    def missing(self) -> list[str]:
        """Leg identifiers still absent, in canonical order."""
        return [leg.leg for leg in self.legs if not leg.present]


async def assess_completeness(
    *,
    session: Any,
    ca_firm_id: uuid.UUID,
    client_id: uuid.UUID,
    trade_name: str,
    month: int,
    year: int,
) -> ClientCompleteness:
    """Assess the three data legs for one client + filing period.

    Firm-scoped, read-only. ``trade_name`` is passed in (the caller has
    already resolved the client) so this makes no extra client lookup.
    """
    books = await _books_leg(
        session=session, ca_firm_id=ca_firm_id, client_id=client_id, month=month, year=year
    )
    gstr2b = await _gstr2b_leg(
        session=session, ca_firm_id=ca_firm_id, client_id=client_id, month=month, year=year
    )
    bank = await _bank_leg(
        session=session, ca_firm_id=ca_firm_id, client_id=client_id
    )
    return ClientCompleteness(
        client_id=client_id,
        trade_name=trade_name,
        month=month,
        year=year,
        legs=(books, gstr2b, bank),
    )


async def assess_firm_completeness(
    *,
    session: Any,
    ca_firm_id: uuid.UUID,
    month: int,
    year: int,
) -> list[ClientCompleteness]:
    """Completeness for every active client in the firm (for the 8c chase).

    Ordered by trade name. Each client is assessed independently; the loop
    is the firm-wide view the proactive-chase cron will iterate.
    """
    clients_q = ClientQuery(session=session, ca_firm_id=ca_firm_id)
    reports: list[ClientCompleteness] = []
    for client in await clients_q.list_active():
        reports.append(
            await assess_completeness(
                session=session,
                ca_firm_id=ca_firm_id,
                client_id=client.id,
                trade_name=client.trade_name,
                month=month,
                year=year,
            )
        )
    return reports


async def _books_leg(
    *,
    session: Any,
    ca_firm_id: uuid.UUID,
    client_id: uuid.UUID,
    month: int,
    year: int,
) -> LegStatus:
    """Books leg — exportable documents for this client in the period."""
    docs_q = DocumentQuery(session=session, ca_firm_id=ca_firm_id)
    period_docs = await docs_q.list_by_filing_period(year=year, month=month)
    count = sum(
        1
        for d in period_docs
        if d.client_id == client_id
        and (d.processing_status or "").lower() in _EXPORTABLE_STATUSES
    )
    return LegStatus(leg=LEG_BOOKS, present=count > 0, count=count)


async def _gstr2b_leg(
    *,
    session: Any,
    ca_firm_id: uuid.UUID,
    client_id: uuid.UUID,
    month: int,
    year: int,
) -> LegStatus:
    """GSTR-2B leg — 2B entries stamped with this return period (MMYYYY)."""
    target = f"{month:02d}{year}"
    brain_q = BrainEventQuery(session=session, ca_firm_id=ca_firm_id)
    events = await brain_q.list_filtered(
        client_id=client_id,
        source=_GSTR2B_SOURCE,
        event_type=_GSTR2B_EVENT,
        limit=5000,
    )
    count = sum(
        1
        for ev in events
        if str((ev.payload if isinstance(ev.payload, dict) else {}).get("return_period") or "")
        == target
    )
    return LegStatus(leg=LEG_GSTR2B, present=count > 0, count=count)


async def _bank_leg(
    *,
    session: Any,
    ca_firm_id: uuid.UUID,
    client_id: uuid.UUID,
) -> LegStatus:
    """Bank leg — any bank transaction ingested for this client.

    Bank statements carry no return-period stamp (the 180-day recon spans
    months), so presence of the payment leg is not period-scoped: a
    statement having arrived at all is what unblocks the recon.
    """
    brain_q = BrainEventQuery(session=session, ca_firm_id=ca_firm_id)
    events = await brain_q.list_filtered(
        client_id=client_id,
        source=_BANK_SOURCE,
        event_type=_BANK_EVENT,
        limit=1,
    )
    count = len(events)
    return LegStatus(leg=LEG_BANK, present=count > 0, count=count)
