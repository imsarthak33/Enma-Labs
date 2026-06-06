"""Tests for the Section 17(5) blocked-credit engine."""

from __future__ import annotations

from decimal import Decimal

from app.tax.blocked_credits import compute_blocked, line_is_blocked_by
from app.tax.types import LineItem, Party, Transaction


def _line(
    hsn: str | None,
    *,
    taxable: str = "1000",
    cgst: str = "90",
    sgst: str = "90",
    igst: str = "0",
) -> LineItem:
    return LineItem(
        description=None,
        hsn_sac=hsn,
        taxable_value=Decimal(taxable),
        cgst_amount=Decimal(cgst),
        sgst_amount=Decimal(sgst),
        igst_amount=Decimal(igst),
    )


def _tx(document_type: str, *lines: LineItem) -> Transaction:
    return Transaction(
        document_type=document_type,
        vendor=Party(gstin=None),
        buyer=Party(gstin=None),
        invoice_date=None,
        line_items=lines,
    )


class TestLineIsBlockedBy:
    def test_restaurant_document_type_blocks_food_beverage(self) -> None:
        line = _line(hsn=None)  # no HSN — match comes from document_type
        cat = line_is_blocked_by(line, "RESTAURANT")
        assert cat is not None
        assert cat.code == "food_beverage"

    def test_motor_vehicle_hsn(self) -> None:
        line = _line(hsn="87031010")
        cat = line_is_blocked_by(line, "B2B_INVOICE")
        assert cat is not None
        assert cat.code == "motor_vehicle"

    def test_unmatched_line(self) -> None:
        line = _line(hsn="1234")
        assert line_is_blocked_by(line, "B2B_INVOICE") is None

    def test_no_hsn_and_no_document_match(self) -> None:
        line = _line(hsn=None)
        assert line_is_blocked_by(line, "B2B_INVOICE") is None


class TestComputeBlocked:
    def test_restaurant_blocks_all_lines(self) -> None:
        tx = _tx("RESTAURANT", _line(hsn="9963"), _line(hsn=None))
        result = compute_blocked(tx)
        # 180 per line x 2 lines = 360.00
        assert result.block_amount == Decimal("360.00")
        assert all(o.blocked for o in result.per_line)
        assert "food_beverage" in result.reasons

    def test_mixed_invoice(self) -> None:
        # One blocked line (motor vehicle) + one legit B2B line.
        tx = _tx(
            "B2B_INVOICE",
            _line(hsn="87031010", cgst="900", sgst="900"),
            _line(hsn="8517", cgst="90", sgst="90"),
        )
        result = compute_blocked(tx)
        # Only the motor vehicle line is blocked. 900+900 = 1800.00.
        assert result.block_amount == Decimal("1800.00")
        assert result.per_line[0].blocked
        assert not result.per_line[1].blocked
        assert result.reasons == ("motor_vehicle",)

    def test_zero_when_nothing_blocked(self) -> None:
        tx = _tx("B2B_INVOICE", _line(hsn="8517"))
        result = compute_blocked(tx)
        assert result.block_amount == Decimal("0.00")
        assert result.reasons == ()

    def test_paise_precision(self) -> None:
        # 17.99 + 18.01 must sum to 36.00 exactly.
        tx = _tx(
            "RESTAURANT",
            _line(hsn="9963", taxable="100", cgst="8.99", sgst="9.00", igst="0"),
            _line(hsn="9963", taxable="100", cgst="9.01", sgst="9.00", igst="0"),
        )
        result = compute_blocked(tx)
        assert result.block_amount == Decimal("36.00")
