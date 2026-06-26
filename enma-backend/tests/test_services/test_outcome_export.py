"""TA-1 tests — outcome-statement CSV export (gather + render)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import outcome_export
from app.services.outcome_export import (
    OutcomeRunRow,
    OutcomeStatementRow,
    build_outcome_statement_csv,
    gather_firm_outcomes,
)


def test_build_csv_has_totals_and_runs() -> None:
    rows = [
        OutcomeStatementRow(
            client_name="S.S Traders",
            recovered_itc=Decimal("94.28"),
            reversal_risk_itc=Decimal("51000.00"),
            runs=(OutcomeRunRow(3, 2026, Decimal("94.28"), Decimal("200.00")),),
        ),
        OutcomeStatementRow(
            client_name="Acme",
            recovered_itc=Decimal("100.00"),
            reversal_risk_itc=Decimal("0"),
        ),
    ]
    raw = build_outcome_statement_csv(rows=rows, firm_name="Sarthak & Associates")
    out = raw.decode("utf-8-sig")
    assert "Outcome statement — Sarthak & Associates" in out
    assert "S.S Traders,94.28,51000.00" in out
    assert "TOTAL,194.28,51000.00" in out  # recovered summed across clients
    assert "03/2026,94.28,200.00" in out  # run detail block


async def test_gather_skips_inactive_clients() -> None:
    active = SimpleNamespace(id=uuid.uuid4(), trade_name="Active")
    quiet = SimpleNamespace(id=uuid.uuid4(), trade_name="Quiet")

    sums = {active.id: {"itc_recovered_inr": Decimal("500.00")}}
    outcomes = MagicMock()
    outcomes.sum_by_kind = AsyncMock(
        side_effect=lambda *, kind, client_id=None: sums.get(client_id, {}).get(
            kind, Decimal("0")
        )
    )
    runs = MagicMock()
    runs.list_recent = AsyncMock(return_value=[])

    with (
        patch.object(outcome_export, "OutcomeUnitQuery", return_value=outcomes),
        patch.object(outcome_export, "ReconRunQuery", return_value=runs),
    ):
        rows = await gather_firm_outcomes(
            session=AsyncMock(),
            ca_firm_id=uuid.uuid4(),
            clients=[active, quiet],
        )

    assert [r.client_name for r in rows] == ["Active"]  # Quiet has no activity
    assert rows[0].recovered_itc == Decimal("500.00")
