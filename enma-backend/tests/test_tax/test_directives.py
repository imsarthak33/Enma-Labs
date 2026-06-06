"""Tests for the typed :class:`RuleDirective` surface."""

from __future__ import annotations

import pytest
from app.tax.directives import (
    DirectiveAction,
    DirectiveScope,
    RuleDirective,
    ScopeKind,
)
from pydantic import ValidationError

_VALID_GSTIN = "27AABCU9603R1ZM"  # shape-valid; checksum not asserted here.


class TestDirectiveScope:
    def test_client_wide_no_value(self) -> None:
        scope = DirectiveScope(kind=ScopeKind.CLIENT_WIDE)
        assert scope.value is None

    def test_client_wide_rejects_value(self) -> None:
        with pytest.raises(ValidationError, match="CLIENT_WIDE"):
            DirectiveScope(kind=ScopeKind.CLIENT_WIDE, value="oops")

    def test_vendor_gstin_requires_15_chars(self) -> None:
        with pytest.raises(ValidationError, match="15-char"):
            DirectiveScope(kind=ScopeKind.VENDOR_GSTIN, value="27AAA")

    def test_vendor_gstin_happy(self) -> None:
        scope = DirectiveScope(kind=ScopeKind.VENDOR_GSTIN, value=_VALID_GSTIN)
        assert scope.value == _VALID_GSTIN

    def test_hsn_prefix_must_be_digits(self) -> None:
        with pytest.raises(ValidationError, match="digits"):
            DirectiveScope(kind=ScopeKind.HSN_PREFIX, value="ABCD")

    def test_document_type_requires_value(self) -> None:
        with pytest.raises(ValidationError, match="requires a value"):
            DirectiveScope(kind=ScopeKind.DOCUMENT_TYPE, value="")

    def test_frozen(self) -> None:
        scope = DirectiveScope(kind=ScopeKind.CLIENT_WIDE)
        with pytest.raises(ValidationError):
            scope.value = "mutated"  # type: ignore[misc]


class TestRuleDirective:
    def test_force_block_vendor(self) -> None:
        d = RuleDirective(
            action=DirectiveAction.FORCE_BLOCK,
            scope=DirectiveScope(kind=ScopeKind.VENDOR_GSTIN, value=_VALID_GSTIN),
            note="Vendor under composition scheme — never claim ITC.",
        )
        payload = d.to_jsonb()
        assert payload["action"] == "force_block"
        assert payload["scope"]["kind"] == "vendor_gstin"

    def test_set_gst_tds_deductor_requires_client_wide(self) -> None:
        with pytest.raises(ValidationError, match="CLIENT_WIDE"):
            RuleDirective(
                action=DirectiveAction.SET_GST_TDS_DEDUCTOR,
                scope=DirectiveScope(kind=ScopeKind.VENDOR_GSTIN, value=_VALID_GSTIN),
            )

    def test_set_gst_tds_deductor_client_wide_ok(self) -> None:
        d = RuleDirective(
            action=DirectiveAction.SET_GST_TDS_DEDUCTOR,
            scope=DirectiveScope(kind=ScopeKind.CLIENT_WIDE),
        )
        assert d.action is DirectiveAction.SET_GST_TDS_DEDUCTOR

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RuleDirective.model_validate(
                {
                    "action": "force_block",
                    "scope": {"kind": "client_wide"},
                    "uninvited_guest": 42,
                }
            )

    def test_roundtrip(self) -> None:
        original = RuleDirective(
            action=DirectiveAction.FORCE_RCM,
            scope=DirectiveScope(kind=ScopeKind.HSN_PREFIX, value="9965"),
        )
        roundtripped = RuleDirective.from_jsonb(original.to_jsonb())
        assert roundtripped == original

    def test_from_jsonb_empty_is_none(self) -> None:
        assert RuleDirective.from_jsonb(None) is None
        assert RuleDirective.from_jsonb({}) is None

    def test_unknown_action_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RuleDirective.model_validate(
                {
                    "action": "delete_universe",
                    "scope": {"kind": "client_wide"},
                }
            )

    def test_note_length_capped(self) -> None:
        with pytest.raises(ValidationError):
            RuleDirective(
                action=DirectiveAction.NOTE,
                scope=DirectiveScope(kind=ScopeKind.CLIENT_WIDE),
                note="x" * 501,
            )
