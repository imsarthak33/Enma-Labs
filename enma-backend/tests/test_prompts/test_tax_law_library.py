"""Tests for the expanded Phase 5 tax-law library."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.prompts.tax_law_library import (
    BLOCKED_CATEGORIES,
    DOCUMENT_TYPES,
    GST_TDS_THRESHOLDS,
    RCM_TRIGGERS,
    EngineModule,
    module_for_document_type,
    modules_for_verdict,
)


class TestDocumentTypeContext:
    @pytest.mark.parametrize("dt", DOCUMENT_TYPES)
    def test_every_type_has_paragraph(self, dt: str) -> None:
        paragraph = module_for_document_type(dt)
        assert paragraph
        # Sanity: phase 5 paragraphs carry statutory anchors for the
        # types where law actually applies.
        if dt in {"B2B_INVOICE", "RESTAURANT", "FREIGHT", "PROFESSIONAL", "CAPITAL_GOODS"}:
            assert "Section" in paragraph

    def test_unknown_fallback(self) -> None:
        assert module_for_document_type("INVENTED_TYPE") == module_for_document_type("UNKNOWN")


class TestModulesForVerdict:
    def test_b2b_runs_all_four(self) -> None:
        mods = modules_for_verdict("B2B_INVOICE")
        assert mods == {
            EngineModule.BLOCKED,
            EngineModule.RCM,
            EngineModule.TDS,
            EngineModule.ITC,
        }

    def test_b2c_and_unknown_empty(self) -> None:
        assert modules_for_verdict("B2C_INVOICE") == frozenset()
        assert modules_for_verdict("UNKNOWN") == frozenset()
        # Unknown / unmapped types fall through to the empty set too.
        assert modules_for_verdict("INVENTED") == frozenset()

    def test_restaurant_only_blocked(self) -> None:
        assert modules_for_verdict("RESTAURANT") == {EngineModule.BLOCKED}

    def test_freight_runs_rcm_tds_itc(self) -> None:
        assert modules_for_verdict("FREIGHT") == {
            EngineModule.RCM,
            EngineModule.TDS,
            EngineModule.ITC,
        }


class TestBlockedCategories:
    def test_has_motor_food_club(self) -> None:
        codes = {b.code for b in BLOCKED_CATEGORIES}
        assert {"motor_vehicle", "food_beverage", "club_membership"} <= codes

    def test_all_carry_citation(self) -> None:
        for cat in BLOCKED_CATEGORIES:
            assert cat.citation.startswith("Section")
            assert cat.hsn_prefixes

    def test_restaurant_food_link(self) -> None:
        food = next(b for b in BLOCKED_CATEGORIES if b.code == "food_beverage")
        assert food.document_type_match == "RESTAURANT"


class TestRcmTriggers:
    def test_gta_has_forward_charge_threshold(self) -> None:
        gta = next(t for t in RCM_TRIGGERS if t.code == "gta_freight")
        assert gta.forward_charge_rate == Decimal("12")
        assert gta.document_type_match == "FREIGHT"

    def test_legal_services_hsn(self) -> None:
        legal = next(t for t in RCM_TRIGGERS if t.code == "legal_services")
        assert "9982" in legal.hsn_prefixes


class TestGstTdsThreshold:
    def test_threshold_is_2_5_lakh(self) -> None:
        assert GST_TDS_THRESHOLDS.threshold_inr == Decimal("250000")

    def test_rate_is_2_percent(self) -> None:
        assert GST_TDS_THRESHOLDS.rate_percent == Decimal("2")
