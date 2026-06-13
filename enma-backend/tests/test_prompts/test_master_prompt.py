"""Tests asserting prompt-builder structure and the single-source-of-truth rule."""

from __future__ import annotations

import pytest
from app.prompts.identity import ROLE_DESCRIPTION, VOICE_INSTRUCTIONS
from app.prompts.master_prompt import (
    build_classifier_prompt,
    build_extractor_prompt,
)
from app.prompts.tax_law_library import DOCUMENT_TYPES, module_for_document_type


class TestClassifierPrompt:
    def test_includes_identity_and_voice(self) -> None:
        prompt = build_classifier_prompt()
        assert ROLE_DESCRIPTION in prompt
        assert VOICE_INSTRUCTIONS in prompt

    def test_lists_every_document_type(self) -> None:
        prompt = build_classifier_prompt()
        for dt in DOCUMENT_TYPES:
            assert dt in prompt

    def test_demands_json_only(self) -> None:
        prompt = build_classifier_prompt()
        assert "JSON" in prompt
        assert "No markdown" in prompt
        assert "No code fences" in prompt


class TestExtractorPrompt:
    @pytest.mark.parametrize("dt", DOCUMENT_TYPES)
    def test_includes_law_context_for_each_type(self, dt: str) -> None:
        prompt = build_extractor_prompt(dt)
        # The relevant law module's text must appear verbatim in the prompt.
        assert module_for_document_type(dt) in prompt

    def test_includes_schema_fields(self) -> None:
        """The schema string must mention every field the reconciler reads.

        After O, ``observed_totals`` replaces the LLM-computed ``totals``
        block, and the per-line ``*_amount`` fields for CGST / SGST / IGST
        are dropped — the Python reconciler computes them from
        rate × canonical taxable so the LLM never has to do math.
        """
        prompt = build_extractor_prompt("B2B_INVOICE")
        for required_field in (
            "vendor",
            "buyer",
            "line_items",
            "observed_totals",
            "label",
            "amount",
            "line_amount",
            "tax_amount",
            "cgst_rate",
            "sgst_rate",
            "igst_rate",
            "invoice_date",
        ):
            assert required_field in prompt, f"missing {required_field} in schema"

    def test_unknown_type_falls_back_gracefully(self) -> None:
        prompt = build_extractor_prompt("MADE_UP_TYPE")
        # Should not raise; should include the UNKNOWN law context.
        assert "UNKNOWN DOCUMENT" in prompt
