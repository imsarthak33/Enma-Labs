"""Master prompt builders — the only place LLM system prompts are composed.

Every agent (classifier, extractor, supervisor) calls a function here to
get its system-message string. No agent file embeds prompt text. The
benefits:

* Reviewer can read one file to audit what the model is told.
* Prompt iteration ships as a single PR diff, not a hunt across modules.
* Tests assert prompt structure without spinning up agents.

Composition pattern
-------------------
Each builder concatenates four blocks in fixed order:

    1. Role / identity        (from :mod:`identity`)
    2. Voice instructions     (from :mod:`identity`)
    3. Stage-specific task    (defined in this file)
    4. Output schema / format (defined in this file)

That ordering matters: the LLM gives more weight to instructions at the
end of the system message in practice.
"""

from __future__ import annotations

from typing import Final

from app.prompts.identity import ROLE_DESCRIPTION, VOICE_INSTRUCTIONS
from app.prompts.tax_law_library import DOCUMENT_TYPES, module_for_document_type

__all__ = [
    "build_classifier_prompt",
    "build_extractor_prompt",
]


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


_CLASSIFIER_TASK: Final[str] = (
    "TASK: CLASSIFY DOCUMENT TYPE\n"
    "You will receive a single document (image or PDF page) attached to "
    "the next user turn. Identify which canonical category it falls into. "
    "If the document is genuinely ambiguous or illegible, return "
    "UNKNOWN — do not guess."
)

_CLASSIFIER_OUTPUT: Final[str] = (
    "OUTPUT FORMAT\n"
    "Respond with a single JSON object exactly matching this schema:\n"
    "{\n"
    '  "document_type": "<one of: ' + " | ".join(DOCUMENT_TYPES) + '>",\n'
    '  "confidence": "HIGH" | "MEDIUM" | "LOW",\n'
    '  "reasoning": "<one-sentence justification, max 200 chars>"\n'
    "}\n"
    "Return ONLY the JSON object. No prose. No markdown. No code fences."
)


def build_classifier_prompt() -> str:
    """System message for the document-classifier agent."""
    return "\n\n".join(
        [
            ROLE_DESCRIPTION,
            VOICE_INSTRUCTIONS,
            _CLASSIFIER_TASK,
            _CLASSIFIER_OUTPUT,
        ]
    )


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


_EXTRACTOR_TASK_TEMPLATE: Final[str] = (
    "TASK: EXTRACT STRUCTURED FIELDS\n"
    "The user has uploaded a {document_type} document. Extract every "
    "field listed in the OUTPUT FORMAT section below. Do not hallucinate "
    "values that are not visibly present — emit null if a field is "
    "missing or illegible.\n\n"
    "RELEVANT LAW CONTEXT\n"
    "{law_context}\n\n"
    "EXTRACTION RULES\n"
    "- Currency: numeric value only (no symbols, no commas).\n"
    "- Dates: ISO format YYYY-MM-DD.\n"
    "- GSTINs: 15-character canonical form (uppercase, no spaces).\n"
    "- Line items: list every visible row, even if amounts are 0."
)


_EXTRACTOR_OUTPUT_SCHEMA: Final[str] = (
    "OUTPUT FORMAT\n"
    "Respond with a single JSON object matching this schema:\n"
    "{\n"
    '  "vendor": {\n'
    '    "name": "<string or null>",\n'
    '    "gstin": "<15-char GSTIN or null>",\n'
    '    "address": "<string or null>"\n'
    "  },\n"
    '  "buyer": {\n'
    '    "name": "<string or null>",\n'
    '    "gstin": "<15-char GSTIN or null>",\n'
    '    "address": "<string or null>"\n'
    "  },\n"
    '  "invoice_number": "<string or null>",\n'
    '  "invoice_date": "<YYYY-MM-DD or null>",\n'
    '  "line_items": [\n'
    "    {\n"
    '      "description": "<string>",\n'
    '      "hsn_sac": "<string or null>",\n'
    '      "quantity": "<numeric string or null>",\n'
    '      "unit_price": "<numeric string or null>",\n'
    '      "taxable_value": "<numeric string>",\n'
    '      "cgst_rate": "<numeric string or null>",\n'
    '      "cgst_amount": "<numeric string or null>",\n'
    '      "sgst_rate": "<numeric string or null>",\n'
    '      "sgst_amount": "<numeric string or null>",\n'
    '      "igst_rate": "<numeric string or null>",\n'
    '      "igst_amount": "<numeric string or null>"\n'
    "    }\n"
    "  ],\n"
    '  "totals": {\n'
    '    "taxable_value": "<numeric string>",\n'
    '    "total_cgst": "<numeric string>",\n'
    '    "total_sgst": "<numeric string>",\n'
    '    "total_igst": "<numeric string>",\n'
    '    "grand_total": "<numeric string>"\n'
    "  }\n"
    "}\n"
    "Return ONLY the JSON object. No prose. No markdown. No code fences."
)


def build_extractor_prompt(document_type: str) -> str:
    """System message for the extractor, parameterised by document type.

    ``document_type`` should be one of :data:`DOCUMENT_TYPES`; an unknown
    value falls back to the ``UNKNOWN`` law-context paragraph so the
    function never raises.
    """
    task = _EXTRACTOR_TASK_TEMPLATE.format(
        document_type=document_type,
        law_context=module_for_document_type(document_type),
    )
    return "\n\n".join(
        [
            ROLE_DESCRIPTION,
            VOICE_INSTRUCTIONS,
            task,
            _EXTRACTOR_OUTPUT_SCHEMA,
        ]
    )
