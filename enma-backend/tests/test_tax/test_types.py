"""Tests for the engine-side typed shape + extraction → Transaction projection."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.tax.types import LineItem, Party, transaction_from_extraction


def _make_extraction(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "vendor": {"name": "Acme Logistics", "gstin": "27AABCU9603R1ZM"},
        "buyer": {"name": "Client Co", "gstin": "29AAACS1234B1Z5"},
        "invoice_date": "2025-04-15",
        "line_items": [
            {
                "description": "Freight",
                "hsn_sac": "9965",
                "taxable_value": "10000",
                "cgst_amount": "250",
                "sgst_amount": "250",
                "igst_amount": "0",
            }
        ],
    }
    base.update(overrides)
    return base


class TestExtractionProjection:
    def test_happy_path(self) -> None:
        tx = transaction_from_extraction(_make_extraction(), document_type="FREIGHT")
        assert tx.document_type == "FREIGHT"
        assert tx.vendor == Party(gstin="27AABCU9603R1ZM", name="Acme Logistics")
        assert tx.invoice_date == date(2025, 4, 15)
        assert len(tx.line_items) == 1
        line = tx.line_items[0]
        assert line.taxable_value == Decimal("10000.00")
        assert line.total_tax == Decimal("500.00")
        assert line.has_intra_state_tax
        assert not line.has_igst

    def test_missing_invoice_date(self) -> None:
        tx = transaction_from_extraction(
            _make_extraction(invoice_date=None), document_type="B2B_INVOICE"
        )
        assert tx.invoice_date is None

    def test_malformed_amounts_become_zero(self) -> None:
        ext = _make_extraction(
            line_items=[
                {
                    "description": "weird",
                    "hsn_sac": "9965",
                    "taxable_value": "not-a-number",
                    "cgst_amount": None,
                    "sgst_amount": "₹ 1,000.50",
                    "igst_amount": "0",
                }
            ]
        )
        tx = transaction_from_extraction(ext, document_type="FREIGHT")
        line = tx.line_items[0]
        assert line.taxable_value == Decimal("0.00")
        assert line.cgst_amount == Decimal("0.00")
        # Currency-decorated strings parse via decimal_utils.parse_money.
        assert line.sgst_amount == Decimal("1000.50")

    def test_vendor_gstin_normalised(self) -> None:
        ext = _make_extraction(vendor={"name": "v", "gstin": "  27aabcu9603r1zm  "})
        tx = transaction_from_extraction(ext, document_type="B2B_INVOICE")
        assert tx.vendor.gstin == "27AABCU9603R1ZM"

    def test_empty_line_items(self) -> None:
        ext = _make_extraction(line_items=[])
        tx = transaction_from_extraction(ext, document_type="B2B_INVOICE")
        assert tx.line_items == ()
        assert tx.gross_taxable_value == Decimal("0.00")

    def test_non_dict_line_skipped(self) -> None:
        ext = _make_extraction(line_items=["nope", {"taxable_value": "100"}])
        tx = transaction_from_extraction(ext, document_type="B2B_INVOICE")
        assert len(tx.line_items) == 1


class TestLineItemFlags:
    def test_intra_state_flag(self) -> None:
        line = LineItem(
            description=None,
            hsn_sac=None,
            taxable_value=Decimal("100"),
            cgst_amount=Decimal("9"),
            sgst_amount=Decimal("9"),
            igst_amount=Decimal("0"),
        )
        assert line.has_intra_state_tax
        assert not line.has_igst

    def test_inter_state_flag(self) -> None:
        line = LineItem(
            description=None,
            hsn_sac=None,
            taxable_value=Decimal("100"),
            cgst_amount=Decimal("0"),
            sgst_amount=Decimal("0"),
            igst_amount=Decimal("18"),
        )
        assert line.has_igst
        assert not line.has_intra_state_tax
