"""Tests — the Phase 9 GSTR-3B draft assembler + dormant GSP submission."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from app.services.filing import gstr3b as g3b
from app.services.filing.gstr3b import build_gstr3b_draft
from app.services.providers.gsp import NullGspClient

FIRM_ID = uuid.uuid4()
CLIENT_ID = uuid.uuid4()


def _sales_event(*, taxable: str, cgst: str, sgst: str, igst: str = "0") -> SimpleNamespace:
    total = Decimal(taxable) + Decimal(cgst) + Decimal(sgst) + Decimal(igst)
    entries = [
        {"ledger_name": "Sales Account", "amount": taxable, "is_party": False},
        {"ledger_name": "Output CGST", "amount": cgst, "is_party": False},
        {"ledger_name": "Output SGST", "amount": sgst, "is_party": False},
        {"ledger_name": "Party Ledger", "amount": f"-{total}", "is_party": True},
    ]
    if Decimal(igst) > 0:
        entries.insert(1, {"ledger_name": "Output IGST", "amount": igst, "is_party": False})
    return SimpleNamespace(
        occurred_at=datetime(2026, 3, 15, tzinfo=UTC),
        payload={"ledger_entries": entries},
    )


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sales: list[Any],
    runs: list[Any],
) -> None:
    class _FakeBrainQuery:
        def __init__(self, **_kw: Any) -> None: ...

        async def list_filtered(self, **_kw: Any) -> list[Any]:
            return sales

    class _FakeReconRunQuery:
        def __init__(self, **_kw: Any) -> None: ...

        async def list_recent(self, **_kw: Any) -> list[Any]:
            return runs

    monkeypatch.setattr(g3b, "BrainEventQuery", _FakeBrainQuery)
    monkeypatch.setattr(g3b, "ReconRunQuery", _FakeReconRunQuery)


async def _build() -> Any:
    return await build_gstr3b_draft(
        session=object(), ca_firm_id=FIRM_ID, client_id=CLIENT_ID, month=3, year=2026
    )


async def test_outward_tax_and_taxable_value(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        sales=[_sales_event(taxable="1000", cgst="90", sgst="90")],
        runs=[],
    )
    draft = await _build()
    assert draft.outward.taxable_value == Decimal("1000")
    assert draft.outward.cgst == Decimal("90")
    assert draft.outward.sgst == Decimal("90")
    assert draft.outward.total_tax == Decimal("180")
    assert draft.sales_voucher_count == 1


async def test_itc_from_period_recon_and_net_payable(monkeypatch: pytest.MonkeyPatch) -> None:
    run = SimpleNamespace(
        filing_period_month=3, filing_period_year=2026, recoverable_itc=Decimal("50")
    )
    _install(
        monkeypatch,
        sales=[_sales_event(taxable="1000", cgst="90", sgst="90")],
        runs=[run],
    )
    draft = await _build()
    assert draft.itc_available == Decimal("50")
    assert draft.net_tax_payable == Decimal("130")  # 180 tax - 50 ITC


async def test_net_floored_at_zero_when_itc_exceeds_tax(monkeypatch: pytest.MonkeyPatch) -> None:
    run = SimpleNamespace(
        filing_period_month=3, filing_period_year=2026, recoverable_itc=Decimal("500")
    )
    _install(monkeypatch, sales=[_sales_event(taxable="1000", cgst="90", sgst="90")], runs=[run])
    draft = await _build()
    assert draft.net_tax_payable == Decimal("0")
    assert any("floored" in n for n in draft.notes)


async def test_no_recon_gives_zero_itc_with_note(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, sales=[_sales_event(taxable="1000", cgst="90", sgst="90")], runs=[])
    draft = await _build()
    assert draft.itc_available == Decimal("0")
    assert any("No reconciliation" in n for n in draft.notes)


async def test_igst_bucketed_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        sales=[_sales_event(taxable="1000", cgst="0", sgst="0", igst="180")],
        runs=[],
    )
    draft = await _build()
    assert draft.outward.igst == Decimal("180")
    assert draft.outward.cgst == Decimal("0")
    assert draft.outward.taxable_value == Decimal("1000")


async def test_payload_is_json_safe_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, sales=[_sales_event(taxable="1000", cgst="90", sgst="90")], runs=[])
    payload = (await _build()).to_payload()
    assert payload["return_type"] == "GSTR3B"
    assert payload["is_draft"] is True
    assert payload["filing_period"] == "032026"
    # money serialised as strings (normalised to 2 dp)
    assert payload["outward_supplies_3_1_a"]["cgst"] == "90.00"
    assert isinstance(payload["notes"], list)


async def test_gsp_file_return_dormant_by_default() -> None:
    receipt = await NullGspClient().file_return(
        gstin="27AABCU9603R1ZN",
        return_type="GSTR3B",
        return_period="032026",
        auth_token="t",
        payload={},
    )
    assert receipt is None
