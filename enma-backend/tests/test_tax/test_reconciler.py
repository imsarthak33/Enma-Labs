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
    _is_real_discrepancy,
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


def _pranay_like() -> dict[str, object]:
    """Single-line inter-state invoice whose grand-total row prints qty too.

    Mirrors the real Pranay/Kota-Stone invoice: the extractor pulled the
    quantity (7,240 SQFT) from the grand-total row instead of ₹3,25,800.
    """
    return {
        "vendor": {"name": "Pranay Construction", "gstin": "08DWAPS3131P1Z4"},
        "buyer": {"name": "S.S Traders", "gstin": "10CDVPS7198R1Z7"},
        "invoice_number": "13/2025-26",
        "invoice_date": "2026-03-23",
        "line_items": [
            {
                "description": "KOTA STONE",
                "hsn_sac": "2515",
                "quantity": "7240",
                "unit_price": "45",
                "tax_amount": "15514.29",
                "line_amount": "325800",
                "igst_rate": "5",
                "igst_amount": "15514.29",
            }
        ],
        "observed_totals": [
            {"label": "Taxable Amount", "amount": "310285.71"},
            {"label": "IGST @5%", "amount": "15514.29"},
            {"label": "Grand Total", "amount": "7240"},  # qty mis-read
        ],
    }


def _municipal_like() -> dict[str, object]:
    """Intra-state 18% invoice with a per-HSN totals table partially captured.

    Mirrors the real Municipal invoice: the extractor grabbed one HSN row's
    CGST (₹180 of ₹4,383.31) and a partial taxable (₹20,000 of ₹48,703.38).
    """
    return {
        "vendor": {"name": "S.S Traders", "gstin": "10CDVPS7198R1Z7"},
        "buyer": {"name": "Municipal Office", "gstin": "10ABCDE1234F1Z5"},
        "invoice_number": "210",
        "invoice_date": "2026-04-25",
        "line_items": [
            {
                "description": "Electrical goods",
                "hsn_sac": "8544",
                "quantity": "1",
                "unit_price": "48703.38",
                "tax_amount": "8766.61",
                "line_amount": "57469.99",
                "cgst_rate": "9",
                "sgst_rate": "9",
            }
        ],
        "observed_totals": [
            {"label": "Taxable Amount", "amount": "20000"},  # partial capture
            {"label": "CGST @9%", "amount": "180"},  # one HSN row only
            {"label": "SGST @9%", "amount": "180"},
        ],
    }


class TestOcrMisreadSuppression:
    """OCR column-confusion in observed totals must not raise false flags."""

    def test_grand_total_qty_misread_not_flagged(self) -> None:
        result = reconcile_extraction(_pranay_like())
        codes = {d.code for d in result.discrepancies}
        assert "grand_total_mismatch" not in codes
        # Canonical math is still correct.
        assert result.grand_total == Decimal("325800.00")

    def test_partial_cgst_and_taxable_not_flagged(self) -> None:
        result = reconcile_extraction(_municipal_like())
        codes = {d.code for d in result.discrepancies}
        assert "cgst_mismatch" not in codes
        assert "sgst_mismatch" not in codes
        assert "taxable_mismatch" not in codes
        # Computed taxable from line math is authoritative.
        assert result.taxable == Decimal("48703.38")

    def test_small_genuine_discrepancy_still_flagged(self) -> None:
        # A real printed-total error (₹50 off on a ₹9,750 invoice) is small
        # relative to the total → it must STILL surface.
        inv = _sudha_invoice()
        inv["observed_totals"] = [
            {"label": "Taxable Amount", "amount": "9285.71"},
            {"label": "CGST @2.5%", "amount": "232.14"},
            {"label": "SGST @2.5%", "amount": "232.14"},
            {"label": "Total Amount", "amount": "9700"},  # ₹50 short — real
        ]
        result = reconcile_extraction(inv)
        codes = {d.code for d in result.discrepancies}
        assert "grand_total_mismatch" in codes


class TestIsRealDiscrepancy:
    """Unit coverage for the OCR-misread suppression helper."""

    @pytest.mark.parametrize(
        ("observed", "computed", "expected"),
        [
            (Decimal("7240"), Decimal("325800"), False),     # qty mis-read
            (Decimal("180"), Decimal("4383.31"), False),     # partial tax row
            (Decimal("20000"), Decimal("48703.38"), False),  # partial taxable
            (Decimal("9700"), Decimal("9749.99"), True),     # real ₹50 error
            (Decimal("9750"), Decimal("9749.99"), False),    # within tolerance
        ],
    )
    def test_thresholds(self, observed: Decimal, computed: Decimal, expected: bool) -> None:
        assert _is_real_discrepancy(observed, computed) is expected

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
        # State codes must DIFFER for the reconciler to allocate to IGST.
        # 27 = Maharashtra, 10 = Bihar.
        extraction = {
            "vendor": {"gstin": "27AAAAA0000A1Z5"},
            "buyer": {"gstin": "10BBBBB0000B1Z5"},
            "line_items": [
                {
                    "quantity": "10",
                    "unit_price": "1000",
                    "line_amount": "11800",
                    "tax_amount": "1800",
                    "tax_label": "18%",
                }
            ],
            "observed_totals": [
                {"label": "Taxable Amount", "amount": "10000"},
                {"label": "IGST @18%", "amount": "1800"},
                {"label": "Total Amount", "amount": "11800"},
            ],
        }
        result = reconcile_extraction(extraction)
        assert result.intra_state is False
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
        ("label", "tax_amount_str", "expected_rate"),
        [
            ("CGST @2.5%", "2.5", Decimal("2.5")),
            ("CGST 2.5%", "2.5", Decimal("2.5")),
            ("cgst@9%", "9", Decimal("9")),
            ("CGST @ 18%", "18", Decimal("18")),
        ],
    )
    def test_observed_cgst_label_cross_check_succeeds(
        self, label: str, tax_amount_str: str, expected_rate: Decimal
    ) -> None:
        """Observed CGST labels sum and cross-check against the computed
        per-line CGST total. v2's regex captures the rate purely for
        display + discrepancy messages; the amount sums are what drive
        the actual reconciliation.
        """
        # Per-line tax_amount represents the COMBINED CGST+SGST (2x the
        # observed CGST rate). This keeps the per-line math sane.
        line_tax = str(Decimal(tax_amount_str) * 2)
        extraction = {
            "line_items": [
                {
                    "quantity": "1",
                    "unit_price": "100",
                    "line_amount": str(Decimal("100") + Decimal(line_tax)),
                    "tax_amount": line_tax,
                    "tax_label": f"{Decimal(tax_amount_str) * 2}%",
                }
            ],
            "observed_totals": [
                {"label": "Taxable Amount", "amount": "100"},
                {"label": label, "amount": tax_amount_str},
                {"label": f"S{label[1:]}", "amount": tax_amount_str},
            ],
        }
        result = reconcile_extraction(extraction)
        # Aggregate single rate: tax_rate / 2 = the observed CGST rate.
        assert result.cgst_rate == expected_rate
        # No cgst_mismatch: observed sum equals computed.
        codes = {d.code for d in result.discrepancies}
        assert "cgst_mismatch" not in codes


class TestReconcilerMixedRateInvoice:
    """The real MAA JANKI TRADERS invoice — six line items, two rates,
    intra-state. Production caught reconciler v1 picking ONE rate and
    applying it to the entire taxable, inflating CGST by 5×."""

    @staticmethod
    def _maa_janki() -> dict[str, object]:
        # All Bihar (state code 10) — intra-state, CGST + SGST only.
        return {
            "vendor": {"name": "CLEIND PRODUCT & SERVICE", "gstin": "10DYKPR4180P1ZV"},
            "buyer": {"name": "MAA JANKI TRADERS", "gstin": "10AJYPD6904B2ZK"},
            "invoice_number": "127",
            "invoice_date": "2025-08-17",
            "line_items": [
                # Line 1: 18% — EASY CLIP MOP, 100 PCS @ ₹10
                {
                    "description": "EASY CLIP MOP 9\"",
                    "hsn_sac": "9603",
                    "quantity": "100",
                    "unit_price": "10",
                    "tax_label": "18%",
                    "tax_amount": "180",
                    "line_amount": "1180",
                    # The LLM hallucinated igst_rate here in production —
                    # reconciler must ignore it because the state codes
                    # match (both 10 = Bihar) → intra-state.
                    "igst_rate": "5",
                },
                # Line 2: 5% — BOOS FLOOR DUSTER
                {
                    "description": "BOOS FLOOR DUSTER 24x24",
                    "hsn_sac": "6307",
                    "quantity": "64",
                    "unit_price": "209.52",
                    "tax_label": "5%",
                    "tax_amount": "670.48",
                    "line_amount": "14080",
                },
                # Line 3: 18%
                {
                    "quantity": "100",
                    "unit_price": "21.19",
                    "tax_label": "18%",
                    "tax_amount": "381.36",
                    "line_amount": "2500",
                },
                # Line 4: 18%
                {
                    "quantity": "100",
                    "unit_price": "20",
                    "tax_label": "18%",
                    "tax_amount": "360",
                    "line_amount": "2360",
                },
                # Line 5: 5%
                {
                    "quantity": "150",
                    "unit_price": "47.62",
                    "tax_label": "5%",
                    "tax_amount": "357.14",
                    "line_amount": "7500",
                },
                # Line 6: 5%
                {
                    "quantity": "50",
                    "unit_price": "160",
                    "tax_label": "5%",
                    "tax_amount": "400",
                    "line_amount": "8400",
                },
            ],
            "observed_totals": [
                {"label": "Subtotal", "amount": "36020"},
                {"label": "Taxable Amount", "amount": "33671.03"},
                {"label": "CGST @2.5%", "amount": "713.81"},
                {"label": "SGST @2.5%", "amount": "713.81"},
                {"label": "CGST @9%", "amount": "460.68"},
                {"label": "SGST @9%", "amount": "460.68"},
                {"label": "Total Amount", "amount": "36020"},
            ],
        }

    def test_intra_state_detected_from_matching_gstin_prefixes(self) -> None:
        result = reconcile_extraction(self._maa_janki())
        assert result.intra_state is True

    def test_no_igst_on_intra_state_invoice(self) -> None:
        """Even though line 1 carries a hallucinated igst_rate=5, the
        reconciler must ignore it because the state codes match."""
        result = reconcile_extraction(self._maa_janki())
        assert result.igst_amount == Decimal("0.00")

    def test_per_line_canonical_amounts(self) -> None:
        """Line 1 at 18% intra → CGST=90, SGST=90. Line 2 at 5% intra → CGST=335.24, SGST=335.24."""
        result = reconcile_extraction(self._maa_janki())
        line_1 = result.line_items[0]
        line_2 = result.line_items[1]
        # Line 1: taxable 1000, rate 18% → CGST 90, SGST 90.
        assert line_1.taxable == Decimal("1000.00")
        assert line_1.tax_rate == Decimal("18")
        assert line_1.cgst_amount == Decimal("90.00")
        assert line_1.sgst_amount == Decimal("90.00")
        assert line_1.igst_amount == Decimal("0")  # hallucinated igst_rate ignored
        # Line 2: taxable 13409.52, rate 5% → CGST 335.24, SGST 335.24.
        assert line_2.taxable == Decimal("13409.52")
        assert line_2.tax_rate == Decimal("5")
        assert line_2.cgst_amount == Decimal("335.24")
        assert line_2.sgst_amount == Decimal("335.24")

    def test_aggregate_totals_sum_per_line(self) -> None:
        result = reconcile_extraction(self._maa_janki())
        # 18% lines sum to 1000+2118.64+2000 = 5118.64; CGST 9% → 460.68.
        # 5%  lines sum to 13409.52+7142.86+8000 = 28552.38; CGST 2.5% → 713.81.
        # Total CGST = 460.68 + 713.81 = 1174.49.
        assert result.cgst_amount == Decimal("1174.49")
        assert result.sgst_amount == Decimal("1174.49")
        assert result.igst_amount == Decimal("0.00")
        # Total taxable = 33671.02 (line items). Observed says 33671.03 — within tolerance.
        assert result.taxable == Decimal("33671.03")
        # Grand = 33671.03 + 1174.49 + 1174.49 = 36020.01.
        assert abs(result.grand_total - Decimal("36020.00")) <= Decimal("0.05")

    def test_no_taxable_mismatch_when_within_tolerance(self) -> None:
        result = reconcile_extraction(self._maa_janki())
        codes = {d.code for d in result.discrepancies}
        # The line-items-sum (33671.02) is within 1 paisa of the observed
        # (33671.03), so no mismatch should fire.
        assert "taxable_mismatch" not in codes
        # Aggregate CGST/SGST also match the observed sums.
        assert "cgst_mismatch" not in codes
        assert "sgst_mismatch" not in codes

    def test_rate_breakdown_shows_both_slices(self) -> None:
        """Summary needs to render 'CGST 1,174.49 (713.81 @ 2.5% + 460.68 @ 9%)'."""
        result = reconcile_extraction(self._maa_janki())
        rates = {entry[0] for entry in result.rate_breakdown}
        assert Decimal("5") in rates
        assert Decimal("18") in rates
        # Aggregate single rate must NOT be set (it's a multi-rate invoice).
        assert result.cgst_rate == Decimal("0")


class TestReconcilerInterStateDetection:
    """When state codes differ, allocate to IGST instead of CGST+SGST."""

    def test_inter_state_invoice_routes_to_igst(self) -> None:
        extraction = {
            "vendor": {"name": "V", "gstin": "27AAAAA0000A1Z5"},  # 27 = Maharashtra
            "buyer": {"name": "B", "gstin": "10BBBBB0000B1Z5"},  # 10 = Bihar
            "line_items": [
                {
                    "quantity": "10",
                    "unit_price": "100",
                    "tax_label": "18%",
                    "tax_amount": "180",
                    "line_amount": "1180",
                }
            ],
        }
        result = reconcile_extraction(extraction)
        assert result.intra_state is False
        assert result.cgst_amount == Decimal("0.00")
        assert result.sgst_amount == Decimal("0.00")
        assert result.igst_amount == Decimal("180.00")
        assert result.grand_total == Decimal("1180.00")


class TestReconcilerDataclass:
    """Smoke checks on the output type — frozen, hashable, dict-dumpable."""

    def test_reconciled_invoice_is_frozen(self) -> None:
        result = reconcile_extraction(_sudha_invoice())
        assert isinstance(result, ReconciledInvoice)
        with pytest.raises(Exception):  # noqa: B017 — frozen=True raises FrozenInstanceError
            result.taxable = Decimal("0")  # type: ignore[misc]
