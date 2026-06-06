"""Tests for the Section 51 GST-TDS engine."""

from __future__ import annotations

from decimal import Decimal

from app.tax.tds import compute_tds
from app.tax.types import LineItem, Party, Transaction


def _tx(*, taxable: str) -> Transaction:
    return Transaction(
        document_type="B2B_INVOICE",
        vendor=Party(gstin=None),
        buyer=Party(gstin=None),
        invoice_date=None,
        line_items=(
            LineItem(
                description=None,
                hsn_sac=None,
                taxable_value=Decimal(taxable),
                cgst_amount=Decimal("0"),
                sgst_amount=Decimal("0"),
                igst_amount=Decimal("0"),
            ),
        ),
    )


def test_not_applied_when_recipient_not_deductor() -> None:
    result = compute_tds(_tx(taxable="500000"), gst_tds_deductor=False)
    assert result.tds_amount == Decimal("0.00")
    assert not result.applied
    assert result.reason == "recipient_not_deductor"


def test_not_applied_below_threshold() -> None:
    # 249,999 < 250,000 threshold.
    result = compute_tds(_tx(taxable="249999"), gst_tds_deductor=True)
    assert result.tds_amount == Decimal("0.00")
    assert not result.applied
    assert result.reason == "below_threshold"


def test_applied_at_exact_threshold() -> None:
    result = compute_tds(_tx(taxable="250000"), gst_tds_deductor=True)
    # 2% of 250,000 = 5,000.00.
    assert result.tds_amount == Decimal("5000.00")
    assert result.applied
    assert result.reason == "section_51"


def test_paise_precision() -> None:
    # 2% of 299,999.99 = 5,999.9998 → quantises HALF_UP to 6,000.00.
    result = compute_tds(_tx(taxable="299999.99"), gst_tds_deductor=True)
    assert result.tds_amount == Decimal("6000.00")
    assert result.applied
