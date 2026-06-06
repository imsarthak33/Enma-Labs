"""Tests for the firm-rule context injector."""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.agents.context_injector import format_rules_for_prompt
from app.agents.tax_engine import ConsultedRule
from app.tax.directives import (
    DirectiveAction,
    DirectiveScope,
    RuleDirective,
    ScopeKind,
)


def _rule(rule_id: uuid.UUID, directive: RuleDirective | None = None) -> ConsultedRule:
    return ConsultedRule(
        rule_id=rule_id,
        source="human_correction",
        similarity=Decimal("0.9"),
        directive=directive,
        applied=False,
    )


class TestFormatRulesForPrompt:
    def test_empty_input(self) -> None:
        out = format_rules_for_prompt((), rule_texts={})
        assert "No firm-specific rules" in out

    def test_renders_text_and_directive(self) -> None:
        rid = uuid.uuid4()
        directive = RuleDirective(
            action=DirectiveAction.FORCE_BLOCK,
            scope=DirectiveScope(kind=ScopeKind.VENDOR_GSTIN, value="27AABCU9603R1ZM"),
        )
        rules = (_rule(rid, directive),)
        out = format_rules_for_prompt(rules, rule_texts={rid: "Never claim ITC from Acme"})
        assert "Never claim ITC from Acme" in out
        assert "force_block" in out
        assert "vendor_gstin" in out

    def test_no_markdown(self) -> None:
        rid = uuid.uuid4()
        out = format_rules_for_prompt((_rule(rid),), rule_texts={rid: "rule body"})
        # No markdown emphasis characters in the output. We allow inline
        # backticks-style only if they appear in the user's rule_text;
        # the renderer itself emits none.
        for token in ("**", "__", "###", ">"):
            assert token not in out

    def test_missing_rule_text_fallback(self) -> None:
        rid = uuid.uuid4()
        out = format_rules_for_prompt((_rule(rid),), rule_texts={})
        assert "(no text)" in out
