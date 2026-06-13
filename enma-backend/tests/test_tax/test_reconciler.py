"""Tests for the Python-driven invoice reconciler.

The headline fixture is the real SUDHA invoice that twice caught the
extractor LLM hallucinating in production:

  Vendor: CLEIND PRODUCT & SERVICE
  Buyer:  SUDHA ENTERPRISES
  Line:   BROOM BIG, 150 PCS @ ₹61.9047619... (printed as 61.9)
          Tax 5% = ₹464.29, Amount ₹9,750.00
  Totals: Taxable ₹9,285.71, CGST @2.5% ₹232.14, SGST @2.5% ₹232.14,
          Total ₹9,750.00

The reconciler MUST derive a canonical taxable of ₹9,285.71 from
``line_amount - tax_amount``, recompute CGST/SGST/IGST from rates,
and the grand total to ₹9,750.00 — without ever trusting the LLM's
``totals.taxable_value`` (the hallucinated field that flipped tax and
taxable in production).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.tax.reconciler import (
    ReconciledInvoice,
    reconcile_extraction,
)


def _sudha_invoice() -> dict[str, object]:
    """The real production-failing invoice, as the extractor sees it.

    Note that ``totals.*`` carries the LLM's hallucinated numbers —
    those are intentionally wrong here to lock in that the reconciler
    IGNORES them.
    """
    return {
        "vendor": {"name": "CLEIND PRODUCT & SERVICE", "gstin": "10DYKPR4180P1ZV"},
        "buyer": {"name": "SUDHA ENTERPRISES", "gstin": "10AMEPL5872B1ZI"},
        "invoice_number": "120",
        "invoice_date": "2025-08-06",
        "line_items": [
            {
                "description": "BROOM BIG",
                "hsn_sac": "9603",
                "quantity": "150",
                "unit_price": "61.9",
                "tax_amount": "464.29",
                "line_amount": "9750",
                # The next four are the LLM's hallucinated per-line tax
                # split. The reconciler must NOT base canonical math on
                # these — the totals come from the observed_totals labels
                # plus the line_amount-tax_amount derivation.
                "taxable_value": "464.29",  # WRONG (this is the tax)
                "cgst_rate": "2.5",
                "cgst_amount": "232.14",
                "sgst_rate": "2.5",
                "sgst_amount": "232.14",
            }
        ],
        "observed_totals": [
            {"label": "Subtotal", "amount": "9750"},
            {"label": "Taxable Amount", "amount": "9285.71"},
            {"label": "CGST @2.5%", "amount": "232.14"},
            {"label": "SGST @2.5%", "amount": "232.14"},
            {"label": "Total Amount", "amount": "9750"},
        ],
        "totals": {
            # The hallucinated block the LLM populated. Reconciler must
            # ignore it entirely.
            "taxable_value": "464.29",  # WRONG
            "total_cgst": "232.14",
            "total_sgst": "232.14",
            "total_igst": "0",
            "grand_total": "9750",
        },
    }


class TestReconcilerProductionRegression:
    """The SUDHA invoice — the exact production regression."""

    def test_canonical_taxable_matches_invoice_truth(self) -> None:
        result = reconcile_extraction(_sudha_invoice())
        # ₹9,285.71 — the actual Taxable Amount printed on the PDF.
        # The verifier previously computed "should be 11.61" because it
        # trusted the LLM's hallucinated 464.29; we must not regress.
        assert result.taxable == Decimal("9285.71")

    def test_canonical_cgst_is_taxable_times_rate(self) -> None:
        result = reconcile_extraction(_sudha_invoice())
        assert result.cgst_rate == Decimal("2.5")
        # 9285.71 * 2.5% = 232.14 (after 2dp quantise).
        assert result.cgst_amount == Decimal("232.14")

    def test_canonical_sgst_is_taxable_times_rate(self) -> None:
        result = reconcile_extraction(_sudha_invoice())
        assert result.sgst_rate == Decimal("2.5")
        assert result.sgst_amount == Decimal("232.14")

    def test_no_igst_on_intrastate_invoice(self) -> None:
        result = reconcile_extraction(_sudha_invoice())
        assert result.igst_amount == Decimal("0.00")

    def test_canonical_grand_total_matches_invoice(self) -> None:
        result = reconcile_extraction(_sudha_invoice())
        # 9285.71 + 232.14 + 232.14 + 0 = 9749.99 ≈ 9750
        # (small rounding ok — observed grand_total label discrepancy
        # is flagged if > tolerance).
        assert result.grand_total == Decimal("9749.99") or result.grand_total == Decimal(
            "9750.00"
        )

    def test_clean_reconciliation_has_no_discrepancies(self) -> None:
        """When line items + observed totals agree, no discrepancies fire."""
        result = reconcile_extraction(_sudha_invoice())
        # Filter to non-grand discrepancies — grand might be ₹0.01 off due
        # to quantise rounding which is acceptable for this fixture.
        non_grand = [d for d in result.discrepancies if d.code != "grand_total_mismatch"]
        assert non_grand == [], f"unexpected discrepancies: {non_grand}"


class TestReconcilerWithoutObservedTotals:
    """Pre-O extractions don't carry observed_totals[]. Reconciler must
    still produce a canonical answer from line items alone — flagging
    nothing as a 'missing labels' problem."""

    def test_no_observed_totals_uses_line_items_only(self) -> None:
        extraction = _sudha_invoice()
        del extraction["observed_totals"]
        result = reconcile_extraction(extraction)
        # line_amount - tax_amount = 9750 - 464.29 = 9285.71
        assert result.taxable == Decimal("9285.71")
        # Rates come from per-line cgst_rate/sgst_rate (the LLM emits
        # these from the invoice's printed "@2.5%" labels — clean).
        assert result.cgst_rate == Decimal("2.5")
        assert result.sgst_rate == Decimal("2.5")
        # CGST = 9285.71 * 2.5% = 232.14
        assert result.cgst_amount == Decimal("232.14")


class TestReconcilerInterstateInvoice:
    """IGST-only invoice (vendor and buyer in different states)."""

    def test_igst_only_invoice(self) -> None:
        extraction = {
            "line_items": [
                {
                    "quantity": "10",
                    "unit_price": "1000",
                    "line_amount": "11800",
                    "tax_amount": "1800",
                    "igst_rate": "18",
                }
            ],
            "observed_totals": [
                {"label": "Taxable Amount", "amount": "10000"},
                {"label": "IGST @18%", "amount": "1800"},
                {"label": "Total Amount", "amount": "11800"},
            ],
        }
        result = reconcile_extraction(extraction)
        assert result.taxable == Decimal("10000.00")
        assert result.cgst_amount == Decimal("0.00")
        assert result.sgst_amount == Decimal("0.00")
        assert result.igst_rate == Decimal("18")
        assert result.igst_amount == Decimal("1800.00")
        assert result.grand_total == Decimal("11800.00")


class TestReconcilerDiscrepancyDetection:
    """Discrepancies are the actual value of the reconciler. Make sure
    each kind is detected and reported."""

    def test_taxable_label_disagrees_with_line_items_sum(self) -> None:
        """The observed label says one thing, line items say another."""
        extraction = {
            "line_items": [
                {
                    "quantity": "10",
                    "unit_price": "100",
                    "line_amount": "1180",
                    "tax_amount": "180",
                }
            ],
            "observed_totals": [
                # Disagrees with line items (1000) by ₹500.
                {"label": "Taxable Amount", "amount": "1500"},
                {"label": "CGST @9%", "amount": "90"},
                {"label": "SGST @9%", "amount": "90"},
                {"label": "Total Amount", "amount": "1180"},
            ],
        }
        result = reconcile_extraction(extraction)
        codes = [d.code for d in result.discrepancies]
        assert "taxable_mismatch" in codes
        # When observed disagrees, the reconciler trusts line items.
        assert result.taxable == Decimal("1000.00")

    def test_cgst_mismatch_when_observed_amount_off(self) -> None:
        """Observed CGST disagrees with rate * taxable."""
        extraction = {
            "line_items": [
                {
                    "quantity": "10",
                    "unit_price": "100",
                    "line_amount": "1180",
                    "tax_amount": "180",
                }
            ],
            "observed_totals": [
                {"label": "Taxable Amount", "amount": "1000"},
                # 1000 * 9% = 90 — but invoice typo printed 95.
                {"label": "CGST @9%", "amount": "95"},
                {"label": "SGST @9%", "amount": "90"},
                {"label": "Total Amount", "amount": "1180"},
            ],
        }
        result = reconcile_extraction(extraction)
        codes = [d.code for d in result.discrepancies]
        assert "cgst_mismatch" in codes
        # We trust rate * taxable, not the typo.
        assert result.cgst_amount == Decimal("90.00")


class TestReconcilerInputs:
    """Edge cases in input handling."""

    def test_missing_line_items_returns_zero_taxable(self) -> None:
        result = reconcile_extraction({"line_items": [], "observed_totals": []})
        assert result.taxable == Decimal("0.00")
        assert result.grand_total == Decimal("0.00")
        assert result.line_items == ()

    def test_quantity_times_unit_price_fallback(self) -> None:
        """When line_amount/tax_amount missing, qty * unit_price drives taxable."""
        extraction = {
            "line_items": [
                {"quantity": "5", "unit_price": "200", "cgst_rate": "2.5", "sgst_rate": "2.5"}
            ]
        }
        result = reconcile_extraction(extraction)
        # 5 * 200 = 1000; CGST 25, SGST 25, grand 1050.
        assert result.taxable == Decimal("1000.00")
        assert result.cgst_amount == Decimal("25.00")
        assert result.grand_total == Decimal("1050.00")

    def test_to_dict_is_json_safe(self) -> None:
        import json

        result = reconcile_extraction(_sudha_invoice())
        # Round-trip via JSON to prove the dict is serialisable.
        assert json.loads(json.dumps(result.to_dict()))


class TestReconcilerLabelParsing:
    """The label regex set covers the label variants we've seen in practice."""

    @pytest.mark.parametrize(
        "label",
        [
            "Taxable Amount",
            "TAXABLE VALUE",
            "Subtotal",
            "Net Amount",
            "Basic Amount",
            "  taxable amount  ",
        ],
    )
    def test_taxable_labels(self, label: str) -> None:
        extraction = {
            "line_items": [{"quantity": "1", "unit_price": "100", "line_amount": "100"}],
            "observed_totals": [{"label": label, "amount": "100"}],
        }
        result = reconcile_extraction(extraction)
        # No discrepancy → the label was recognised as taxable.
        assert all(d.code != "taxable_mismatch" for d in result.discrepancies)

    @pytest.mark.parametrize(
        ("label", "expected_rate"),
        [
            ("CGST @2.5%", Decimal("2.5")),
            ("CGST 2.5%", Decimal("2.5")),
            ("cgst@9%", Decimal("9")),
            ("CGST @ 18%", Decimal("18")),
        ],
    )
    def test_cgst_rate_label_parsing(self, label: str, expected_rate: Decimal) -> None:
        extraction = {
            "line_items": [{"quantity": "1", "unit_price": "100", "line_amount": "100"}],
            "observed_totals": [
                {"label": "Taxable Amount", "amount": "100"},
                {"label": label, "amount": "0"},
            ],
        }
        result = reconcile_extraction(extraction)
        assert result.cgst_rate == expected_rate


class TestReconcilerDataclass:
    """Smoke checks on the output type — frozen, hashable, dict-dumpable."""

    def test_reconciled_invoice_is_frozen(self) -> None:
        result = reconcile_extraction(_sudha_invoice())
        assert isinstance(result, ReconciledInvoice)
        with pytest.raises(Exception):  # noqa: B017 — frozen=True raises FrozenInstanceError
            result.taxable = Decimal("0")  # type: ignore[misc]
