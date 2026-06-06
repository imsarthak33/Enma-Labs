"""Tests for the deterministic supply-kind classifier."""

from __future__ import annotations

from decimal import Decimal

from app.tax.classifier import SupplyKind, supply_kind_of
from app.tax.types import LineItem, Party, Transaction


def _tx(*lines: LineItem) -> Transaction:
    return Transaction(
        document_type="B2B_INVOICE",
        vendor=Party(gstin=None),
        buyer=Party(gstin=None),
        invoice_date=None,
        line_items=lines,
    )


def _line(*, cgst: str = "0", sgst: str = "0", igst: str = "0") -> LineItem:
    return LineItem(
        description=None,
        hsn_sac=None,
        taxable_value=Decimal("100"),
        cgst_amount=Decimal(cgst),
        sgst_amount=Decimal(sgst),
        igst_amount=Decimal(igst),
    )


def test_inter_state_when_any_line_has_igst() -> None:
    assert supply_kind_of(_tx(_line(igst="18"))) is SupplyKind.INTER_STATE


def test_intra_state_when_any_line_has_cgst_sgst() -> None:
    assert supply_kind_of(_tx(_line(cgst="9", sgst="9"))) is SupplyKind.INTRA_STATE


def test_unknown_when_no_tax() -> None:
    assert supply_kind_of(_tx(_line())) is SupplyKind.UNKNOWN


def test_igst_wins_when_mixed() -> None:
    """Verifier rejects this case anyway, but the classifier still picks IGST."""
    tx = _tx(_line(cgst="9", sgst="9"), _line(igst="18"))
    assert supply_kind_of(tx) is SupplyKind.INTER_STATE
