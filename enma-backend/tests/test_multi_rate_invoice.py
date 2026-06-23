"""Regression tests for the multi-rate invoice verifier — Part 2.3.

The canonical test is ``test_aditya_enterprises_multi_rate_invoice_no_false_positive``,
which uses the exact invoice that triggered the original bug: a mixed
5% / 18% invoice where the old single-rate verifier picked up only the
first CGST line (₹247.50) and compared it against the correct sum
(₹396.00), producing a false-positive mismatch.

Additional tests cover:
  * Single-rate invoices (backward compat)
  * Interstate IGST invoices
  * Per-slab transposition detection (the new cross-check)
  * Edge case: tax_breakdown with a slab that has no matching line items
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.extraction_schema import (
    InvoiceExtraction,
    LineItem,
    TaxSlabEntry,
)
from app.services.verification.invoice_math_validator import verify_multi_rate_tax


# ---------------------------------------------------------------------------
# The canonical regression test — exact Aditya Enterprises invoice
# ---------------------------------------------------------------------------


def test_aditya_enterprises_multi_rate_invoice_no_false_positive():
    """Invoice 173 has two rate slabs (5% and 18%).

    Before the fix, the verifier compared a single slab's CGST (₹247.50)
    against the computed total (₹396.00) and raised a false-positive.
    After the fix, the verifier sums all slabs and compares sum-to-sum.
    """
    extraction = InvoiceExtraction(
        vendor_name="Cleind Product & Service",
        vendor_gstin="10DYKPR4180P1ZV",
        buyer_name="Aditya Enterprises",
        buyer_gstin="10IFTPS1294Q2ZF",
        invoice_number="173",
        invoice_date="2026-02-18",
        line_items=[
            LineItem(
                description="Cotton Waste Yarn",
                hsn_code="5205",
                quantity=Decimal("35"),
                unit="KGS",
                unit_rate=Decimal("110"),
                gst_rate=Decimal("5"),
                taxable_value=Decimal("9900.00"),
                line_total=Decimal("10395.00"),
            ),
            LineItem(
                description="Cotton Dori Mop",
                hsn_code="96039000",
                quantity=Decimal("30"),
                unit="PCS",
                unit_rate=Decimal("55"),
                gst_rate=Decimal("18"),
                taxable_value=Decimal("1650.00"),
                line_total=Decimal("1947.00"),
            ),
        ],
        tax_breakdown=[
            TaxSlabEntry(
                gst_rate=Decimal("5"),
                taxable_value=Decimal("9900.00"),
                cgst=Decimal("247.50"),
                sgst=Decimal("247.50"),
            ),
            TaxSlabEntry(
                gst_rate=Decimal("18"),
                taxable_value=Decimal("1650.00"),
                cgst=Decimal("148.50"),
                sgst=Decimal("148.50"),
            ),
        ],
        taxable_value_total=Decimal("11550.00"),
        cgst_total=Decimal("396.00"),
        sgst_total=Decimal("396.00"),
        igst_total=Decimal("0.00"),
        grand_total=Decimal("12342.00"),
    )
    result = verify_multi_rate_tax(extraction)
    assert result.passed is True
    assert result.errors == []


# ---------------------------------------------------------------------------
# Backward compat: single-rate invoice
# ---------------------------------------------------------------------------


def test_single_rate_invoice_passes():
    """A single-rate invoice (one slab in tax_breakdown) should pass."""
    extraction = InvoiceExtraction(
        vendor_name="ABC Trading Co.",
        vendor_gstin="27AABCU1234F1Z5",
        buyer_name="XYZ Ltd.",
        buyer_gstin="27BBBCX5678G2Z9",
        invoice_number="INV-001",
        invoice_date="2026-03-15",
        line_items=[
            LineItem(
                description="Office Supplies",
                hsn_code="4820",
                quantity=Decimal("100"),
                unit="PCS",
                unit_rate=Decimal("50"),
                gst_rate=Decimal("18"),
                taxable_value=Decimal("5000.00"),
                line_total=Decimal("5900.00"),
            ),
        ],
        tax_breakdown=[
            TaxSlabEntry(
                gst_rate=Decimal("18"),
                taxable_value=Decimal("5000.00"),
                cgst=Decimal("450.00"),
                sgst=Decimal("450.00"),
            ),
        ],
        taxable_value_total=Decimal("5000.00"),
        cgst_total=Decimal("450.00"),
        sgst_total=Decimal("450.00"),
        igst_total=Decimal("0.00"),
        grand_total=Decimal("5900.00"),
    )
    result = verify_multi_rate_tax(extraction)
    assert result.passed is True
    assert result.errors == []


# ---------------------------------------------------------------------------
# Interstate IGST invoice
# ---------------------------------------------------------------------------


def test_interstate_igst_invoice_passes():
    """Interstate invoice (different state codes) uses IGST."""
    extraction = InvoiceExtraction(
        vendor_name="Delhi Supplier",
        vendor_gstin="07AABCS1234F1Z5",  # Delhi = 07
        buyer_name="Mumbai Buyer",
        buyer_gstin="27BBBCX5678G2Z9",  # Maharashtra = 27
        invoice_number="IGS-100",
        invoice_date="2026-04-10",
        line_items=[
            LineItem(
                description="Consulting Service",
                hsn_code="9982",
                quantity=Decimal("1"),
                unit="JOB",
                unit_rate=Decimal("100000"),
                gst_rate=Decimal("18"),
                taxable_value=Decimal("100000.00"),
                line_total=Decimal("118000.00"),
            ),
        ],
        tax_breakdown=[
            TaxSlabEntry(
                gst_rate=Decimal("18"),
                taxable_value=Decimal("100000.00"),
                igst=Decimal("18000.00"),
            ),
        ],
        taxable_value_total=Decimal("100000.00"),
        cgst_total=Decimal("0.00"),
        sgst_total=Decimal("0.00"),
        igst_total=Decimal("18000.00"),
        grand_total=Decimal("118000.00"),
    )
    result = verify_multi_rate_tax(extraction)
    assert result.passed is True
    assert result.errors == []


# ---------------------------------------------------------------------------
# Per-slab transposition detection
# ---------------------------------------------------------------------------


def test_per_slab_transposition_detected():
    """Two slabs whose totals sum correctly but are individually wrong.

    5% slab CGST should be 247.50 but invoice declares 148.50.
    18% slab CGST should be 148.50 but invoice declares 247.50.
    Grand totals are identical (396.00) — the old aggregate check would
    pass, but the per-slab cross-check catches the swap.
    """
    extraction = InvoiceExtraction(
        vendor_name="Cleind Product & Service",
        vendor_gstin="10DYKPR4180P1ZV",
        buyer_name="Aditya Enterprises",
        buyer_gstin="10IFTPS1294Q2ZF",
        invoice_number="TRANSPOSED",
        invoice_date="2026-02-18",
        line_items=[
            LineItem(
                description="Cotton Waste Yarn",
                hsn_code="5205",
                quantity=Decimal("35"),
                unit="KGS",
                unit_rate=Decimal("110"),
                gst_rate=Decimal("5"),
                taxable_value=Decimal("9900.00"),
                line_total=Decimal("10395.00"),
            ),
            LineItem(
                description="Cotton Dori Mop",
                hsn_code="96039000",
                quantity=Decimal("30"),
                unit="PCS",
                unit_rate=Decimal("55"),
                gst_rate=Decimal("18"),
                taxable_value=Decimal("1650.00"),
                line_total=Decimal("1947.00"),
            ),
        ],
        tax_breakdown=[
            # TRANSPOSED: 5% slab shows 18% slab's CGST/SGST figures
            TaxSlabEntry(
                gst_rate=Decimal("5"),
                taxable_value=Decimal("9900.00"),
                cgst=Decimal("148.50"),  # should be 247.50
                sgst=Decimal("148.50"),  # should be 247.50
            ),
            # TRANSPOSED: 18% slab shows 5% slab's CGST/SGST figures
            TaxSlabEntry(
                gst_rate=Decimal("18"),
                taxable_value=Decimal("1650.00"),
                cgst=Decimal("247.50"),  # should be 148.50
                sgst=Decimal("247.50"),  # should be 148.50
            ),
        ],
        taxable_value_total=Decimal("11550.00"),
        cgst_total=Decimal("396.00"),  # sum is correct!
        sgst_total=Decimal("396.00"),  # sum is correct!
        igst_total=Decimal("0.00"),
        grand_total=Decimal("12342.00"),
    )
    result = verify_multi_rate_tax(extraction)
    assert result.passed is False
    # Should have per-slab errors for both 5% and 18% CGST and SGST
    assert any("5% slab CGST mismatch" in e for e in result.errors)
    assert any("18% slab CGST mismatch" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Orphan slab in tax_breakdown (no matching line items)
# ---------------------------------------------------------------------------


def test_orphan_slab_detected():
    """tax_breakdown declares a 12% slab but no line items carry that rate."""
    extraction = InvoiceExtraction(
        vendor_name="Test Vendor",
        vendor_gstin="27AABCU1234F1Z5",
        buyer_name="Test Buyer",
        buyer_gstin="27BBBCX5678G2Z9",
        invoice_number="ORPHAN-001",
        invoice_date="2026-05-01",
        line_items=[
            LineItem(
                description="Widget",
                hsn_code="8480",
                quantity=Decimal("10"),
                unit="PCS",
                unit_rate=Decimal("100"),
                gst_rate=Decimal("18"),
                taxable_value=Decimal("1000.00"),
                line_total=Decimal("1180.00"),
            ),
        ],
        tax_breakdown=[
            TaxSlabEntry(
                gst_rate=Decimal("18"),
                taxable_value=Decimal("1000.00"),
                cgst=Decimal("90.00"),
                sgst=Decimal("90.00"),
            ),
            # Orphan slab — no line items at 12%
            TaxSlabEntry(
                gst_rate=Decimal("12"),
                taxable_value=Decimal("500.00"),
                cgst=Decimal("30.00"),
                sgst=Decimal("30.00"),
            ),
        ],
        taxable_value_total=Decimal("1500.00"),
        cgst_total=Decimal("120.00"),
        sgst_total=Decimal("120.00"),
        igst_total=Decimal("0.00"),
        grand_total=Decimal("1740.00"),
    )
    result = verify_multi_rate_tax(extraction)
    assert result.passed is False
    assert any("12% slab with no matching line items" in e for e in result.errors)
