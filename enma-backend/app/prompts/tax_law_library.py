"""Codified tax-law reference modules — the single source of legal context.

Phase 4 ships a minimal subset (just what classifier + extractor need to
identify document types). Phase 5 expands to the full Section 17(5) /
RCM / TDS / ITC tables and adds the dispatch table the tax engine uses.

Each module is a short, declarative description an LLM can include in a
system prompt to ground its reasoning. The strings are NOT pulled from
copyrighted commentary — they are paraphrases of statutory text plus
practitioner shorthand.

Public surface:

* :data:`DOCUMENT_TYPES` — the closed enum of document_type values our
  pipeline emits. Used by the classifier prompt AND by the Document
  model's comment so the schema and prompt stay in sync.
* :func:`module_for_document_type` — returns the active tax-law module
  string for the classifier's chosen type. Phase 4 returns a small
  one-paragraph note per type; Phase 5 expands to full rules.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "DOCUMENT_TYPES",
    "module_for_document_type",
]


# ---------------------------------------------------------------------------
# Canonical document_type enum
# ---------------------------------------------------------------------------

# These strings are written to ``documents.document_type``. Adding a new
# value here REQUIRES updating: the classifier prompt, the Document model
# column comment, and any tax-engine dispatch table that switches on type.

DOCUMENT_TYPES: Final[tuple[str, ...]] = (
    "B2B_INVOICE",
    "B2C_INVOICE",
    "FREIGHT",
    "RESTAURANT",
    "IMPORT",
    "PROFESSIONAL",
    "CAPITAL_GOODS",
    "UNKNOWN",
)


# ---------------------------------------------------------------------------
# Minimal per-type law context (Phase 4)
# ---------------------------------------------------------------------------

_MODULES: Final[dict[str, str]] = {
    "B2B_INVOICE": (
        "B2B INVOICE — Section 31 tax invoice. Vendor and buyer are both "
        "registered taxpayers. ITC may be available subject to "
        "Section 16 conditions (possession of invoice, receipt of "
        "goods/services, supplier compliance via GSTR-2B)."
    ),
    "B2C_INVOICE": (
        "B2C INVOICE — Sale to an unregistered person. ITC is generally "
        "not available to the recipient. The supplier may still be "
        "required to remit GST."
    ),
    "FREIGHT": (
        "FREIGHT / GTA — Goods Transport Agency service. Often attracts "
        "Reverse Charge Mechanism (RCM) on the recipient. Verify whether "
        "the GTA has opted to pay tax at 12% under forward charge."
    ),
    "RESTAURANT": (
        "RESTAURANT BILL — Generally a BLOCKED CREDIT under "
        "Section 17(5)(b). ITC is NOT claimable on food and beverages "
        "supplied by a restaurant, hotel, or outdoor caterer unless the "
        "input is used to make an outward taxable supply of the same line."
    ),
    "IMPORT": (
        "IMPORT — Goods or services imported into India. IGST is paid "
        "under reverse charge or via Customs (Bill of Entry). ITC of IGST "
        "is available subject to Section 16."
    ),
    "PROFESSIONAL": (
        "PROFESSIONAL SERVICES — Legal, audit, consultancy, or similar. "
        "Section 16 ITC conditions apply. Check if the supplier is below "
        "the GST registration threshold (in which case no tax invoice)."
    ),
    "CAPITAL_GOODS": (
        "CAPITAL GOODS — Plant, machinery, equipment capitalised in the "
        "books. ITC is available but must be claimed in full in the month "
        "of receipt (no spreading). Common ledger pitfall: claiming as "
        "regular input instead."
    ),
    "UNKNOWN": (
        "UNKNOWN DOCUMENT — Could not confidently classify. Surface the "
        "raw extraction and ask the CA to assign the correct type."
    ),
}


def module_for_document_type(document_type: str) -> str:
    """Return the law-context paragraph for ``document_type``.

    Falls back to the ``UNKNOWN`` paragraph if the type isn't recognised
    — never raises, so a prompt-builder can call this with whatever the
    classifier emits without an extra guard.
    """
    return _MODULES.get(document_type, _MODULES["UNKNOWN"])
