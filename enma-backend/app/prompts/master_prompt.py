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
    "build_supervisor_prompt",
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
    '      "unit_price": "<numeric string from the RATE column or null>",\n'
    '      "tax_amount": "<numeric string from the TAX column or null>",\n'
    '      "line_amount": "<numeric string from the AMOUNT column or null>",\n'
    '      "cgst_rate": "<numeric string or null>",\n'
    '      "sgst_rate": "<numeric string or null>",\n'
    '      "igst_rate": "<numeric string or null>"\n'
    "    }\n"
    "  ],\n"
    '  "observed_totals": [\n'
    "    {\n"
    '      "label": "<verbatim label from the totals block,'
    ' e.g. \\"Taxable Amount\\" or \\"CGST @2.5%\\">",\n'
    '      "amount": "<numeric string>"\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "\n"
    "DO NOT DO MATH. Python will compute the canonical totals from the\n"
    "raw cells you extracted. Specifically:\n"
    "  * Per line: copy what you see in each column verbatim. Do NOT\n"
    "    compute taxable_value = quantity * unit_price yourself.\n"
    "  * Do NOT split a line's tax_amount into CGST/SGST yourself.\n"
    "  * For ``observed_totals``: copy the bottom-of-invoice totals\n"
    "    block as a list of {label, amount} entries verbatim. Include\n"
    "    EVERY labelled total: 'Subtotal', 'Taxable Amount', 'CGST @X%',\n"
    "    'SGST @X%', 'IGST @X%', 'Total Amount', 'Round Off', etc.\n"
    "    Preserve the printed label exactly so Python can parse the rate.\n"
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


# ---------------------------------------------------------------------------
# Supervisor (Phase 6)
# ---------------------------------------------------------------------------


_SUPERVISOR_TASK: Final[str] = (
    "TASK: ANSWER A CA'S TEXT QUERY\n"
    "You receive a free-form Telegram message from a practising CA. "
    "Read the message, decide intent, and act:\n\n"
    "* If the CA is greeting you, thanking you, asking what you can do, "
    "or otherwise chatting — reply warmly in one sentence. Do NOT call "
    "a database tool for casual conversation.\n"
    "* If the CA is asking for facts about their firm (documents, "
    "clients, tasks, filings) — call the read tools and answer.\n"
    "* If the CA is telling you to do something to the firm's records "
    "('add ABC Corp as a client', 'set CLEIND's GSTIN to X', 'mark "
    "doc abc as approved', 'from now on all CLEIND invoices use 5%') "
    "— call the matching mutating tool. Confirm the action in the "
    "reply, do not just acknowledge.\n\n"
    "TOOL-CALLING RULES\n"
    "- Call tools when you need facts — never invent values.\n"
    "- Chain tools when one result narrows the next (resolve a client "
    "name first, then query that client's documents).\n"
    "- Stop calling tools as soon as you have enough to answer.\n"
    "- After the final tool result, produce the user-facing reply.\n\n"
    "TOOL RESULT HANDLING — CRITICAL\n"
    "Tool results come back as JSON. They are INTERNAL DATA, not user "
    "content. NEVER copy a tool result into your reply. ALWAYS "
    "paraphrase the result into natural English for the CA. Examples:\n"
    '  Tool: get_client_status → {"found": true, "client": '
    '{"trade_name": "ABC Corp"}, "open_task_count": 3}\n'
    "  WRONG reply: " '{"found": true, "client": ...}' "\n"
    "  RIGHT reply: ABC Corp is on file — 3 open tasks.\n"
    "  Tool: export_to_tally → "
    '{"error": "Filing for 06/2026 has not been approved yet. Run '
    'ENMA APPROVE FILING for this period first, then export."}' "\n"
    '  WRONG reply: {"error": "Filing for 06/2026..."}\n'
    "  RIGHT reply: ⚠ I can't export June 2026 yet — that filing "
    "isn't approved. Send <b>ENMA APPROVE FILING for June 2026</b> "
    "first, then I'll export.\n"
    "  Tool: export_to_tally → "
    '{"exported": true, "client": "CLEIND", "voucher_count": 12}\n'
    '  WRONG reply: {"exported": true, ...}\n'
    "  RIGHT reply: ✅ Sent the Tally XML for CLEIND — 12 vouchers.\n"
    "If a tool returns an error field, do NOT call the same tool "
    "again. Explain the error to the CA in one sentence and tell them "
    "what to do next.\n"
)


_SUPERVISOR_OUTPUT: Final[str] = (
    "OUTPUT FORMAT\n"
    "Your final assistant message is sent to the user via Telegram with "
    "HTML parse mode. You may use light HTML formatting (<b>, <i>, "
    "<code>) and one status emoji at the head of a line (✅ ⚠ ❌ 📊). "
    "Do NOT use markdown, decorative emojis inside body text, <html> "
    "or <body> tags."
)


def build_supervisor_prompt(
    *,
    firm_name: str | None = None,
    ca_name: str | None = None,
    firm_rules_block: str | None = None,
) -> str:
    """System message for the supervisor agent.

    ``firm_name`` and ``ca_name`` are injected as a FIRM IDENTITY block
    so the LLM can answer "what is my firm name?" without hallucinating
    or calling a tool. Both are optional — the supervisor still works
    when identity is unavailable (legacy callers, tests).

    ``firm_rules_block`` is the rendered output of
    :func:`app.agents.context_injector.format_rules_for_prompt` for the
    active firm + (optionally) client. Passing ``None`` is fine — the
    supervisor still works without any firm-specific rules.
    """
    parts = [
        ROLE_DESCRIPTION,
        VOICE_INSTRUCTIONS,
        _SUPERVISOR_TASK,
        _SUPERVISOR_OUTPUT,
    ]
    # Firm identity — always present when the caller supplies it so the
    # LLM can answer "who am I?" / "what firm is this?" immediately.
    if firm_name:
        identity_lines = [f"FIRM IDENTITY\nFirm name: {firm_name}"]
        if ca_name:
            identity_lines.append(f"CA / principal: {ca_name}")
        identity_lines.append(
            "You are acting on behalf of this firm. When the user asks "
            "about their firm name or who they are, use the details above."
        )
        parts.append("\n".join(identity_lines))
    if firm_rules_block:
        parts.append("CONTEXT — FIRM-SPECIFIC RULES\n" + firm_rules_block)
    return "\n\n".join(parts)
