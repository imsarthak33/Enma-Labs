"""Section 17(5) blocked-credit checker.

For each line item we ask: does this line fall into a blocked category?
Match strategy:

  1. **Document-type match** — if the transaction's ``document_type``
     equals a category's ``document_type_match`` (e.g. RESTAURANT →
     food_beverage), every line on the document is blocked.
  2. **HSN/SAC prefix match** — if the line's HSN/SAC starts with any
     of the category's prefixes, that line is blocked.

When a line is blocked, its **total tax** (CGST+SGST+IGST) is added to
``block_amount`` and removed from any later ITC computation. The reason
code is the category's ``code`` (e.g. ``motor_vehicle``).

Pure Python. No LLM. Decimal arithmetic only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.prompts.tax_law_library import BLOCKED_CATEGORIES, BlockedCategory
from app.tax.types import LineItem, Transaction
from app.utils.decimal_utils import ZERO, quantize_money

__all__ = [
    "BlockedResult",
    "LineBlockOutcome",
    "compute_blocked",
    "line_is_blocked_by",
]


@dataclass(frozen=True)
class LineBlockOutcome:
    """Why a single line was blocked (or wasn't)."""

    line_index: int
    blocked: bool
    blocked_tax: Decimal
    category_code: str | None = None
    citation: str | None = None


@dataclass(frozen=True)
class BlockedResult:
    """Aggregate of the per-line block decisions."""

    block_amount: Decimal
    per_line: tuple[LineBlockOutcome, ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)


def line_is_blocked_by(line: LineItem, document_type: str) -> BlockedCategory | None:
    """Return the first category that blocks ``line``, or None.

    Document-type matches outrank HSN matches (so RESTAURANT documents
    always block via food_beverage, even if a line's HSN is missing).
    """
    for cat in BLOCKED_CATEGORIES:
        if cat.document_type_match and cat.document_type_match == document_type:
            return cat
    hsn = (line.hsn_sac or "").strip()
    if not hsn:
        return None
    for cat in BLOCKED_CATEGORIES:
        for prefix in cat.hsn_prefixes:
            if hsn.startswith(prefix):
                return cat
    return None


def compute_blocked(transaction: Transaction) -> BlockedResult:
    """Compute the block portion of the verdict for ``transaction``."""
    outcomes: list[LineBlockOutcome] = []
    block_total: Decimal = ZERO
    reason_codes: set[str] = set()
    for idx, line in enumerate(transaction.line_items):
        category = line_is_blocked_by(line, transaction.document_type)
        if category is None:
            outcomes.append(LineBlockOutcome(line_index=idx, blocked=False, blocked_tax=ZERO))
            continue
        line_tax = line.total_tax
        outcomes.append(
            LineBlockOutcome(
                line_index=idx,
                blocked=True,
                blocked_tax=line_tax,
                category_code=category.code,
                citation=category.citation,
            )
        )
        block_total += line_tax
        reason_codes.add(category.code)
    return BlockedResult(
        block_amount=quantize_money(block_total),
        per_line=tuple(outcomes),
        reasons=tuple(sorted(reason_codes)),
    )
