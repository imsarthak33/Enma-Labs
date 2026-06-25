"""TA-1 tests — the outcome_summary supervisor tool (the Track-A meter).

Pure-ish: the DB query objects are mocked, so this exercises the tool's
aggregation + response shaping without a live Postgres.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.supervisor import SupervisorContext, _tool_outcome_summary


def _ctx() -> SupervisorContext:
    return SupervisorContext(session=AsyncMock(), ca_firm_id=uuid.uuid4(), chat_id=1)


def _run_row() -> SimpleNamespace:
    return SimpleNamespace(
        filing_period_month=8,
        filing_period_year=2025,
        matched_count=5,
        amount_mismatch_count=1,
        in_books_not_in_2b_count=2,
        in_2b_not_in_books_count=1,
        recoverable_itc=Decimal("94.28"),
        at_risk_itc=Decimal("200.00"),
    )


async def test_outcome_summary_firmwide_aggregates_totals() -> None:
    sums = {
        "itc_recovered_inr": Decimal("94.28"),
        "itc_reversal_risk_inr": Decimal("51000.00"),
        "reconciled_period": Decimal("3"),
    }
    outcomes = MagicMock()
    outcomes.sum_by_kind = AsyncMock(
        side_effect=lambda *, kind, client_id=None: sums[kind]
    )
    runs = MagicMock()
    runs.list_recent = AsyncMock(return_value=[_run_row()])

    with (
        patch("app.agents.supervisor.OutcomeUnitQuery", return_value=outcomes),
        patch("app.agents.supervisor.ReconRunQuery", return_value=runs),
    ):
        res = await _tool_outcome_summary(_ctx(), {})

    assert res["scope"] == "all clients"
    assert res["recovered_itc_total"] == "94.28"
    assert res["reversal_risk_itc_total"] == "51000.00"
    assert res["periods_reconciled"] == 3
    assert len(res["recent_runs"]) == 1
    row = res["recent_runs"][0]
    assert row["month"] == 8
    assert row["year"] == 2025
    assert row["recoverable_itc"] == "94.28"
    assert row["at_risk_itc"] == "200.00"
    # at-risk is shown per period, never folded into a lifetime total.
    assert "at_risk_itc_total" not in res
    # firm-wide → list_recent called without a client filter.
    runs.list_recent.assert_awaited_once_with(limit=12, client_id=None)


async def test_outcome_summary_scopes_to_one_client() -> None:
    client = SimpleNamespace(id=uuid.uuid4(), trade_name="S.S Traders")
    outcomes = MagicMock()
    outcomes.sum_by_kind = AsyncMock(return_value=Decimal("0"))
    runs = MagicMock()
    runs.list_recent = AsyncMock(return_value=[])

    with (
        patch("app.agents.supervisor._resolve_client_from_args", AsyncMock(return_value=client)),
        patch("app.agents.supervisor.OutcomeUnitQuery", return_value=outcomes),
        patch("app.agents.supervisor.ReconRunQuery", return_value=runs),
    ):
        res = await _tool_outcome_summary(_ctx(), {"client_name": "S.S Traders"})

    assert res["scope"] == "S.S Traders"
    # client filter threaded into both the outcome sum and the run list.
    runs.list_recent.assert_awaited_once_with(limit=12, client_id=client.id)
    for call in outcomes.sum_by_kind.await_args_list:
        assert call.kwargs["client_id"] == client.id
