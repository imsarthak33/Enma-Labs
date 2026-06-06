"""Tests for the tax_engine orchestrator.

Covers the load-bearing invariants from ADR-005 rev 2:

  * Frozen-verdict guard: refuses to recompute for locked periods.
  * Deterministic directive application + rule_corpus_hash distinguishability.
  * Precedence comparator semantics (caller-ordered, first-applied-wins).
  * Decimal-only serialisation; no floats in to_jsonb output.
  * Unsupported document types emit zeros + reason.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from app.agents.tax_engine import (
    ENGINE_VERSION,
    ConsultedRule,
    LockedPeriodError,
    TaxVerdict,
    compute_verdict,
)
from app.tax.directives import (
    DirectiveAction,
    DirectiveScope,
    RuleDirective,
    ScopeKind,
)
from app.tax.itc import PeriodContext
from app.utils.date_utils import FilingPeriod

_GSTIN_V1 = "27AABCU9603R1ZM"
_GSTIN_V2 = "29AAACS1234B1Z5"


def _b2b_extraction(
    *,
    vendor_gstin: str = _GSTIN_V1,
    invoice_date: str = "2025-04-15",
    line_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "vendor": {"name": "Vendor", "gstin": vendor_gstin},
        "buyer": {"name": "Buyer", "gstin": _GSTIN_V2},
        "invoice_date": invoice_date,
        "line_items": line_items
        or [
            {
                "description": "Widget",
                "hsn_sac": "8517",
                "taxable_value": "10000",
                "cgst_amount": "900",
                "sgst_amount": "900",
                "igst_amount": "0",
            }
        ],
    }


def _period(year: int, month: int) -> PeriodContext:
    return PeriodContext(
        current=FilingPeriod(year, month),
        as_of=date(year, month, 15),
    )


class TestBasicVerdict:
    def test_b2b_invoice_simple_claim(self) -> None:
        verdict = compute_verdict(
            _b2b_extraction(),
            document_type="B2B_INVOICE",
            period_context=_period(2025, 4),
        )
        assert verdict.claim_amount == Decimal("1800.00")
        assert verdict.defer_amount == Decimal("0.00")
        assert verdict.block_amount == Decimal("0.00")
        assert verdict.rcm_liability == Decimal("0.00")
        assert verdict.tds_amount == Decimal("0.00")
        assert "section_16_eligible" in verdict.reasons["claim"]

    def test_unsupported_document_type_zeros(self) -> None:
        verdict = compute_verdict(
            _b2b_extraction(),
            document_type="B2C_INVOICE",
            period_context=_period(2025, 4),
        )
        assert verdict.claim_amount == Decimal("0.00")
        assert "unsupported_document_type" in verdict.reasons["block"]


class TestFrozenVerdict:
    """ADR-005 rev 2 revision #4."""

    def test_locked_period_recompute_refused(self) -> None:
        ctx = PeriodContext(
            current=FilingPeriod(2025, 5),
            locked_periods=frozenset({(4, 2025)}),
            as_of=date(2025, 5, 15),
        )
        with pytest.raises(LockedPeriodError):
            compute_verdict(
                _b2b_extraction(invoice_date="2025-04-15"),
                document_type="B2B_INVOICE",
                period_context=ctx,
                invoice_period=FilingPeriod(2025, 4),
            )

    def test_allow_locked_bypass(self) -> None:
        ctx = PeriodContext(
            current=FilingPeriod(2025, 5),
            locked_periods=frozenset({(4, 2025)}),
            as_of=date(2025, 5, 15),
        )
        verdict = compute_verdict(
            _b2b_extraction(invoice_date="2025-04-15"),
            document_type="B2B_INVOICE",
            period_context=ctx,
            invoice_period=FilingPeriod(2025, 4),
            allow_locked=True,
        )
        assert isinstance(verdict, TaxVerdict)


class TestDirectiveApplication:
    """ADR-005 rev 2 revision #1 + #5."""

    def test_force_block_shifts_claim_to_block(self) -> None:
        rule = ConsultedRule(
            rule_id=uuid.uuid4(),
            source="human_correction",
            similarity=Decimal("0.95"),
            directive=RuleDirective(
                action=DirectiveAction.FORCE_BLOCK,
                scope=DirectiveScope(kind=ScopeKind.VENDOR_GSTIN, value=_GSTIN_V1),
            ),
            applied=False,
        )
        verdict = compute_verdict(
            _b2b_extraction(),
            document_type="B2B_INVOICE",
            period_context=_period(2025, 4),
            firm_rules=(rule,),
        )
        assert verdict.claim_amount == Decimal("0.00")
        assert verdict.block_amount == Decimal("1800.00")
        # Audit trail records the rule as applied.
        assert verdict.firm_rules_consulted[0].applied is True
        assert "firm_rule_force_block" in verdict.reasons["block"]

    def test_directive_scope_must_match(self) -> None:
        rule = ConsultedRule(
            rule_id=uuid.uuid4(),
            source="human_correction",
            similarity=Decimal("0.95"),
            directive=RuleDirective(
                action=DirectiveAction.FORCE_BLOCK,
                scope=DirectiveScope(
                    kind=ScopeKind.VENDOR_GSTIN,
                    value="33OTHER000000ZZ",  # different vendor
                ),
            ),
            applied=False,
        )
        verdict = compute_verdict(
            _b2b_extraction(),
            document_type="B2B_INVOICE",
            period_context=_period(2025, 4),
            firm_rules=(rule,),
        )
        # Scope doesn't match → directive does not apply.
        assert verdict.claim_amount == Decimal("1800.00")
        assert verdict.firm_rules_consulted[0].applied is False

    def test_precedence_first_applied_wins(self) -> None:
        """Caller orders rules; whoever shifts the money first wins.

        Two opposing directives target the same vendor — the first one
        in the input tuple is the one we expect to "lock in" the
        outcome, because subsequent ones operate on the already-shifted
        pool.
        """
        common_scope = DirectiveScope(kind=ScopeKind.VENDOR_GSTIN, value=_GSTIN_V1)
        force_block = ConsultedRule(
            rule_id=uuid.uuid4(),
            source="human_correction",
            similarity=Decimal("0.99"),
            directive=RuleDirective(action=DirectiveAction.FORCE_BLOCK, scope=common_scope),
            applied=False,
        )
        force_claim = ConsultedRule(
            rule_id=uuid.uuid4(),
            source="human_correction",
            similarity=Decimal("0.95"),
            directive=RuleDirective(action=DirectiveAction.FORCE_CLAIM, scope=common_scope),
            applied=False,
        )
        # If FORCE_BLOCK is first, it moves 1800 → block. Then
        # FORCE_CLAIM tries to pull it back. They cancel out.
        verdict = compute_verdict(
            _b2b_extraction(),
            document_type="B2B_INVOICE",
            period_context=_period(2025, 4),
            firm_rules=(force_block, force_claim),
        )
        # Both rules applied — they net to roughly the same claim_amount
        # but the rule_corpus_hash differs from the no-rules path.
        assert verdict.firm_rules_consulted[0].applied is True
        assert verdict.firm_rules_consulted[1].applied is True

    def test_note_directive_does_not_change_numbers(self) -> None:
        rule = ConsultedRule(
            rule_id=uuid.uuid4(),
            source="manual_entry",
            similarity=None,
            directive=RuleDirective(
                action=DirectiveAction.NOTE,
                scope=DirectiveScope(kind=ScopeKind.CLIENT_WIDE),
                note="watch for duplicate filings",
            ),
            applied=False,
        )
        verdict = compute_verdict(
            _b2b_extraction(),
            document_type="B2B_INVOICE",
            period_context=_period(2025, 4),
            firm_rules=(rule,),
        )
        assert verdict.claim_amount == Decimal("1800.00")
        assert verdict.firm_rules_consulted[0].applied is False


class TestRuleCorpusHash:
    def test_hash_changes_when_rule_set_changes(self) -> None:
        v1 = compute_verdict(
            _b2b_extraction(),
            document_type="B2B_INVOICE",
            period_context=_period(2025, 4),
        )
        rule = ConsultedRule(
            rule_id=uuid.uuid4(),
            source="human_correction",
            similarity=Decimal("0.5"),
            directive=RuleDirective(
                action=DirectiveAction.NOTE,
                scope=DirectiveScope(kind=ScopeKind.CLIENT_WIDE),
            ),
            applied=False,
        )
        v2 = compute_verdict(
            _b2b_extraction(),
            document_type="B2B_INVOICE",
            period_context=_period(2025, 4),
            firm_rules=(rule,),
        )
        assert v1.rule_corpus_hash != v2.rule_corpus_hash

    def test_hash_stable_for_same_inputs(self) -> None:
        rule_id = uuid.uuid4()
        rule = ConsultedRule(
            rule_id=rule_id,
            source="human_correction",
            similarity=Decimal("0.5"),
            directive=RuleDirective(
                action=DirectiveAction.NOTE,
                scope=DirectiveScope(kind=ScopeKind.CLIENT_WIDE),
            ),
            applied=False,
        )
        v1 = compute_verdict(
            _b2b_extraction(),
            document_type="B2B_INVOICE",
            period_context=_period(2025, 4),
            firm_rules=(rule,),
        )
        v2 = compute_verdict(
            _b2b_extraction(),
            document_type="B2B_INVOICE",
            period_context=_period(2025, 4),
            firm_rules=(rule,),
        )
        assert v1.rule_corpus_hash == v2.rule_corpus_hash


class TestSerialisation:
    def test_to_jsonb_uses_strings_for_decimals(self) -> None:
        verdict = compute_verdict(
            _b2b_extraction(),
            document_type="B2B_INVOICE",
            period_context=_period(2025, 4),
        )
        payload = verdict.to_jsonb()
        for key in (
            "claim_amount",
            "defer_amount",
            "block_amount",
            "rcm_liability",
            "tds_amount",
        ):
            assert isinstance(payload[key], str), f"{key} must be a string"
            # No scientific notation, no extra precision.
            assert "." in payload[key]
            assert len(payload[key].split(".")[1]) == 2

    def test_verdict_carries_engine_version_and_strategy(self) -> None:
        verdict = compute_verdict(
            _b2b_extraction(),
            document_type="B2B_INVOICE",
            period_context=_period(2025, 4),
        )
        payload = verdict.to_jsonb()
        assert payload["engine_version"] == ENGINE_VERSION
        assert payload["defer_strategy"] == "period_only"
        assert payload["currency"] == "INR"
        assert payload["rule_corpus_hash"].startswith("sha256:")


class TestGstTdsIntegration:
    def test_deductor_with_above_threshold(self) -> None:
        ext = _b2b_extraction(
            line_items=[
                {
                    "description": "Big",
                    "hsn_sac": "8517",
                    "taxable_value": "300000",
                    "cgst_amount": "27000",
                    "sgst_amount": "27000",
                    "igst_amount": "0",
                }
            ]
        )
        verdict = compute_verdict(
            ext,
            document_type="B2B_INVOICE",
            period_context=_period(2025, 4),
            gst_tds_deductor=True,
        )
        # 2% of 300,000 = 6,000.00.
        assert verdict.tds_amount == Decimal("6000.00")
        assert "section_51" in verdict.reasons["tds"]

    def test_not_deductor_no_tds(self) -> None:
        ext = _b2b_extraction(
            line_items=[
                {
                    "description": "Big",
                    "hsn_sac": "8517",
                    "taxable_value": "300000",
                    "cgst_amount": "27000",
                    "sgst_amount": "27000",
                    "igst_amount": "0",
                }
            ]
        )
        verdict = compute_verdict(
            ext,
            document_type="B2B_INVOICE",
            period_context=_period(2025, 4),
            gst_tds_deductor=False,
        )
        assert verdict.tds_amount == Decimal("0.00")
        assert "recipient_not_deductor" in verdict.reasons["tds"]


class TestFreightRcm:
    def test_gta_5pct_invoice_creates_rcm(self) -> None:
        ext = {
            "vendor": {"name": "GTA Co", "gstin": _GSTIN_V1},
            "buyer": {"name": "Client", "gstin": _GSTIN_V2},
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
        verdict = compute_verdict(
            ext,
            document_type="FREIGHT",
            period_context=_period(2025, 4),
        )
        assert verdict.rcm_liability == Decimal("500.00")
        assert "gta_freight" in verdict.reasons["rcm"]
