"""Tests for the deterministic verifier."""

from __future__ import annotations

from typing import Any

from app.agents.verifier import (
    VerificationSeverity,
    verify_extraction,
)


# A clean B2B invoice with consistent math — used as a baseline.
def _good_invoice() -> dict[str, Any]:
    return {
        "vendor": {
            "name": "Test Vendor Pvt Ltd",
            "gstin": "29AAAGU0010P1Z5",
        },
        "buyer": {
            "name": "Test Buyer Pvt Ltd",
            "gstin": "27AABCU9603R1ZN",
        },
        "invoice_number": "INV-001",
        "invoice_date": "2026-04-01",
        "line_items": [
            {
                "description": "Widget A",
                "taxable_value": "1000.00",
                "cgst_rate": "9",
                "cgst_amount": "90.00",
                "sgst_rate": "9",
                "sgst_amount": "90.00",
                "igst_rate": None,
                "igst_amount": None,
            },
            {
                "description": "Widget B",
                "taxable_value": "500.00",
                "cgst_rate": "9",
                "cgst_amount": "45.00",
                "sgst_rate": "9",
                "sgst_amount": "45.00",
                "igst_rate": None,
                "igst_amount": None,
            },
        ],
        "totals": {
            "taxable_value": "1500.00",
            "total_cgst": "135.00",
            "total_sgst": "135.00",
            "total_igst": "0.00",
            "grand_total": "1770.00",
        },
    }


class TestCleanExtraction:
    def test_clean_invoice_passes(self) -> None:
        result = verify_extraction(_good_invoice())
        # No ERROR-severity issues.
        assert result.is_clean is True
        errors = [i for i in result.issues if i.severity is VerificationSeverity.ERROR]
        assert errors == []


class TestGstinChecks:
    def test_invalid_vendor_gstin_flagged(self) -> None:
        inv = _good_invoice()
        inv["vendor"]["gstin"] = "29AAAGU0010P1Z6"  # wrong checksum
        result = verify_extraction(inv)
        codes = {i.code for i in result.issues}
        assert "vendor_gstin_invalid" in codes
        assert result.is_clean is False

    def test_missing_vendor_gstin_is_warning(self) -> None:
        inv = _good_invoice()
        inv["vendor"]["gstin"] = None
        result = verify_extraction(inv)
        warns = {i.code for i in result.issues if i.severity is VerificationSeverity.WARNING}
        assert "vendor_gstin_missing" in warns
        # WARNINGs don't break "is_clean".
        assert result.is_clean is True

    def test_missing_buyer_gstin_is_info(self) -> None:
        inv = _good_invoice()
        inv["buyer"]["gstin"] = None
        result = verify_extraction(inv)
        infos = {i.code for i in result.issues if i.severity is VerificationSeverity.INFO}
        assert "buyer_gstin_missing" in infos


class TestTaxCoexistence:
    def test_cgst_plus_igst_on_same_line_flagged(self) -> None:
        inv = _good_invoice()
        inv["line_items"][0]["igst_rate"] = "18"
        inv["line_items"][0]["igst_amount"] = "180.00"
        result = verify_extraction(inv)
        codes = {i.code for i in result.issues}
        assert "tax_coexistence" in codes

    def test_cgst_without_sgst_flagged(self) -> None:
        inv = _good_invoice()
        inv["line_items"][0]["sgst_rate"] = None
        inv["line_items"][0]["sgst_amount"] = None
        result = verify_extraction(inv)
        codes = {i.code for i in result.issues}
        assert "cgst_sgst_mismatch" in codes

    def test_inter_state_igst_is_fine(self) -> None:
        inv = _good_invoice()
        for it in inv["line_items"]:
            it["cgst_rate"] = None
            it["cgst_amount"] = None
            it["sgst_rate"] = None
            it["sgst_amount"] = None
        inv["line_items"][0]["igst_rate"] = "18"
        inv["line_items"][0]["igst_amount"] = "180.00"
        inv["line_items"][1]["igst_rate"] = "18"
        inv["line_items"][1]["igst_amount"] = "90.00"
        inv["totals"]["total_cgst"] = "0.00"
        inv["totals"]["total_sgst"] = "0.00"
        inv["totals"]["total_igst"] = "270.00"
        inv["totals"]["grand_total"] = "1770.00"
        result = verify_extraction(inv)
        errors = [i.code for i in result.issues if i.severity is VerificationSeverity.ERROR]
        assert errors == []


class TestMathChecks:
    def test_wrong_per_line_cgst_flagged(self) -> None:
        inv = _good_invoice()
        inv["line_items"][0]["cgst_amount"] = "100.00"  # should be 90.00
        result = verify_extraction(inv)
        codes = {i.code for i in result.issues}
        assert "cgst_math_mismatch" in codes

    def test_wrong_totals_block_flagged(self) -> None:
        inv = _good_invoice()
        inv["totals"]["total_cgst"] = "200.00"  # actual sum is 135.00
        result = verify_extraction(inv)
        codes = {i.code for i in result.issues}
        assert "total_cgst_sum_mismatch" in codes

    def test_wrong_grand_total_flagged(self) -> None:
        inv = _good_invoice()
        inv["totals"]["grand_total"] = "9999.99"
        result = verify_extraction(inv)
        codes = {i.code for i in result.issues}
        assert "grand_total_mismatch" in codes

    def test_decimal_precision_paise_edge(self) -> None:
        """A 99,999.99 invoice with matching 18% IGST must verify clean.

        Earlier float-based implementations would round 99999.99 * 18 / 100
        to 17999.998200000002 — we want exact decimal arithmetic.
        """
        inv: dict[str, Any] = {
            "vendor": {"gstin": "29AAAGU0010P1Z5"},
            "buyer": {"gstin": "27AABCU9603R1ZN"},
            "line_items": [
                {
                    "description": "Big-ticket item",
                    "taxable_value": "99999.99",
                    "igst_rate": "18",
                    # 99999.99 * 18% = 17999.9982 → quantised to 18000.00
                    "igst_amount": "18000.00",
                }
            ],
            "totals": {
                "taxable_value": "99999.99",
                "total_cgst": "0",
                "total_sgst": "0",
                "total_igst": "18000.00",
                "grand_total": "117999.99",
            },
        }
        result = verify_extraction(inv)
        errors = [i for i in result.issues if i.severity is VerificationSeverity.ERROR]
        assert errors == [], f"unexpected errors: {[e.code for e in errors]}"


class TestShapeRobustness:
    def test_non_dict_input(self) -> None:
        result = verify_extraction("not a dict")  # type: ignore[arg-type]
        codes = {i.code for i in result.issues}
        assert "extraction_not_object" in codes

    def test_missing_line_items_flagged(self) -> None:
        inv = _good_invoice()
        inv["line_items"] = "definitely not a list"  # type: ignore[assignment]
        result = verify_extraction(inv)
        codes = {i.code for i in result.issues}
        assert "line_items_not_list" in codes

    def test_non_object_line_item_flagged(self) -> None:
        inv = _good_invoice()
        inv["line_items"].append("a string, not an object")  # type: ignore[arg-type]
        result = verify_extraction(inv)
        codes = {i.code for i in result.issues}
        assert "line_item_not_object" in codes

    def test_missing_taxable_value_is_warning(self) -> None:
        inv = _good_invoice()
        inv["line_items"][0]["taxable_value"] = None
        result = verify_extraction(inv)
        warns = {i.code for i in result.issues if i.severity is VerificationSeverity.WARNING}
        assert "missing_taxable_value" in warns

    def test_result_to_dict_shape(self) -> None:
        inv = _good_invoice()
        result = verify_extraction(inv)
        d = result.to_dict()
        assert "is_clean" in d
        assert "issues" in d
        assert isinstance(d["issues"], list)
