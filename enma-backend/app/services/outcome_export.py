"""Outcome-statement export — the downloadable TA-1 meter (CSV).

The on-demand counterpart to the monthly digest cron: gathers each client's
billable / at-risk reconciliation outcomes (recovered ITC, 180-day reversal
risk, and the recent reconciliation runs) and renders a CSV a CA can hand to
billing. Read-only over ``outcome_units`` + ``reconciliation_runs`` — it
reports figures already on file and never re-runs a reconciliation.

The gather helper is shared so the supervisor tool, the monthly digest, and
this export all read the meter the same way.
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.db.queries.outcome_units import OutcomeUnitQuery
from app.db.queries.reconciliation_runs import ReconRunQuery
from app.utils.decimal_utils import ZERO

__all__ = [
    "OutcomeRunRow",
    "OutcomeStatementRow",
    "build_outcome_statement_csv",
    "gather_firm_outcomes",
]


@dataclass(frozen=True)
class OutcomeRunRow:
    """One reconciliation run on a client's statement."""

    month: int
    year: int
    recoverable_itc: Decimal
    at_risk_itc: Decimal


@dataclass(frozen=True)
class OutcomeStatementRow:
    """One client's outcome line: lifetime totals + recent runs."""

    client_name: str
    recovered_itc: Decimal
    reversal_risk_itc: Decimal
    runs: tuple[OutcomeRunRow, ...] = field(default_factory=tuple)


async def gather_firm_outcomes(
    *,
    session: Any,
    ca_firm_id: uuid.UUID,
    clients: list[Any],
    run_limit: int = 12,
) -> list[OutcomeStatementRow]:
    """Build per-client outcome rows for a firm (clients with activity only).

    ``clients`` is the caller's active-client list (the caller owns the
    tenant-scoped client query). Clients with no recovered/reversal ITC and
    no reconciliation runs are omitted.
    """
    outcomes_q = OutcomeUnitQuery(session=session, ca_firm_id=ca_firm_id)
    runs_q = ReconRunQuery(session=session, ca_firm_id=ca_firm_id)
    rows: list[OutcomeStatementRow] = []
    for client in clients:
        recovered = await outcomes_q.sum_by_kind(
            kind="itc_recovered_inr", client_id=client.id
        )
        reversal = await outcomes_q.sum_by_kind(
            kind="itc_reversal_risk_inr", client_id=client.id
        )
        recent = await runs_q.list_recent(limit=run_limit, client_id=client.id)
        if recovered <= ZERO and reversal <= ZERO and not recent:
            continue
        rows.append(
            OutcomeStatementRow(
                client_name=client.trade_name,
                recovered_itc=recovered,
                reversal_risk_itc=reversal,
                runs=tuple(
                    OutcomeRunRow(
                        month=r.filing_period_month,
                        year=r.filing_period_year,
                        recoverable_itc=r.recoverable_itc,
                        at_risk_itc=r.at_risk_itc,
                    )
                    for r in recent
                ),
            )
        )
    return rows


def build_outcome_statement_csv(
    *, rows: list[OutcomeStatementRow], firm_name: str
) -> bytes:
    """Render outcome rows as a utf-8-sig CSV (Excel-friendly ₹).

    One section of lifetime per-client totals, then a per-run detail block.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([f"Outcome statement — {firm_name}"])
    writer.writerow([])
    writer.writerow(["Client", "Recovered ITC (Rs)", "Reversal-risk ITC (Rs)"])
    total_recovered = ZERO
    total_reversal = ZERO
    for row in rows:
        total_recovered += row.recovered_itc
        total_reversal += row.reversal_risk_itc
        writer.writerow([row.client_name, f"{row.recovered_itc}", f"{row.reversal_risk_itc}"])
    writer.writerow(["TOTAL", f"{total_recovered}", f"{total_reversal}"])

    writer.writerow([])
    writer.writerow(["Reconciliation runs (point-in-time per period)"])
    writer.writerow(
        ["Client", "Period", "Recoverable ITC (Rs)", "At-risk ITC (Rs)"]
    )
    for row in rows:
        for run in row.runs:
            writer.writerow(
                [
                    row.client_name,
                    f"{run.month:02d}/{run.year}",
                    f"{run.recoverable_itc}",
                    f"{run.at_risk_itc}",
                ]
            )
    return buf.getvalue().encode("utf-8-sig")
