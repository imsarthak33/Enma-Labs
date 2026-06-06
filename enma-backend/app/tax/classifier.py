"""Transaction-level classification — purely deterministic.

The agents/classifier.py uses an LLM to pick a ``document_type``. This
module is the **non-LLM** counterpart the tax engine consults at the
line-item level. Two questions matter:

  1. Is this an inter-state supply? (IGST present)
  2. Does any line carry an HSN/SAC matching a known structural rule
     (blocked category, RCM trigger)?

Both questions are answered by inspection — no model, no I/O.
"""

from __future__ import annotations

from enum import StrEnum

from app.tax.types import Transaction

__all__ = [
    "SupplyKind",
    "supply_kind_of",
]


class SupplyKind(StrEnum):
    """Whether the line is intra-state, inter-state, or unknown."""

    INTRA_STATE = "intra_state"
    INTER_STATE = "inter_state"
    UNKNOWN = "unknown"


def supply_kind_of(transaction: Transaction) -> SupplyKind:
    """Determine intra- vs inter-state supply from line-item tax presence.

    The verifier already enforces the CGST+SGST XOR IGST coexistence
    rule, so by the time we're here the line tax fields are consistent.
    If any line carries IGST, the transaction is inter-state; if any
    carries CGST/SGST, it's intra-state. With no taxed lines at all
    (e.g. fully exempt) the engine reports ``UNKNOWN`` and downstream
    modules treat the transaction as having zero ITC.
    """
    has_igst = any(li.has_igst for li in transaction.line_items)
    has_intra = any(li.has_intra_state_tax for li in transaction.line_items)
    if has_igst:
        return SupplyKind.INTER_STATE
    if has_intra:
        return SupplyKind.INTRA_STATE
    return SupplyKind.UNKNOWN
