"""TA-1 tests — the bank leg records its reversal-risk ITC as an outcome.

The 180-day bank recon now writes an ``itc_reversal_risk_inr`` outcome unit
so the Track-A meter can see the bank leg's billable signal. These tests
mock the delivery + DB seams and assert the outcome write happens (only)
when reversal risk is found.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import bank_recon_runner
from app.services.bank_recon import PurchaseForPayment


def _firm() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4())


def _client() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), trade_name="S.S Traders")


def _old_unpaid_purchase() -> PurchaseForPayment:
    # Invoice from early 2025, no payment evidence → past 180 days as of now.
    return PurchaseForPayment(
        ref="inv1",
        vendor_name="GOLDEN THREAD COMPANY",
        invoice_date=date(2025, 1, 1),
        grand_total=Decimal("51000.00"),
        itc_amount=Decimal("9180.00"),
    )


async def _run(*, purchases: list[PurchaseForPayment]):
    outcomes = MagicMock()
    outcomes.record = AsyncMock()
    with (
        patch.object(
            bank_recon_runner,
            "_purchases_for_payment",
            AsyncMock(return_value=purchases),
        ),
        patch.object(bank_recon_runner.telegram, "send_document", AsyncMock()),
        patch.object(bank_recon_runner, "OutcomeUnitQuery", return_value=outcomes),
    ):
        await bank_recon_runner.bank_reconcile_and_deliver(
            session=AsyncMock(),
            firm=_firm(),
            client=_client(),
            transactions=[],  # no debits → the old invoice stays unpaid
            chat_id=123,
        )
    return outcomes


async def test_reversal_risk_records_outcome_unit() -> None:
    outcomes = await _run(purchases=[_old_unpaid_purchase()])
    outcomes.record.assert_awaited_once()
    kwargs = outcomes.record.await_args.kwargs
    assert kwargs["kind"] == "itc_reversal_risk_inr"
    assert kwargs["quantity"] == Decimal("9180.00")
    assert kwargs["confidence"] == Decimal("0.7")  # advisory, not deterministic


async def test_no_reversal_no_outcome() -> None:
    # No purchases → no reversal risk → no outcome row written.
    outcomes = await _run(purchases=[])
    outcomes.record.assert_not_awaited()
