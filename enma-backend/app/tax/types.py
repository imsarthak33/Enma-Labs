"""Canonical engine-side types — the shared vocabulary of the tax layer.

The extractor emits a free-form JSON dict. The verifier validates math
inside that dict. The tax engine needs a TYPED representation so:

  * Every sub-module (blocked, rcm, tds, itc) receives the same shape.
  * Decimals are parsed exactly once, at the boundary, via the
    :mod:`app.utils.decimal_utils` parser.
  * The orchestrator can pass one immutable object into pure functions.

Types here are intentionally narrow — they hold what the engine *needs*,
not the full extraction blob. Anything richer lives in
``documents.extraction_data`` JSONB and the supervisor agent can query
it.

Nothing in this module touches the database, HTTP, or any LLM client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.utils.date_utils import parse_iso_date
from app.utils.decimal_utils import ZERO, parse_money

__all__ = [
    "LineItem",
    "Party",
    "Transaction",
    "transaction_from_extraction",
]


@dataclass(frozen=True)
class Party:
    """Vendor or buyer — only the fields the engine reads."""

    gstin: str | None
    name: str | None = None


@dataclass(frozen=True)
class LineItem:
    """A single line on the invoice, normalised to Decimal money.

    Missing or unparseable inputs land as ``Decimal('0.00')`` so the
    engine doesn't sprinkle ``None`` checks through every sub-module.
    The verifier (Phase 4) is the place that flags missing fields as
    issues; the engine treats absence as "zero contribution".
    """

    description: str | None
    hsn_sac: str | None
    taxable_value: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal

    @property
    def total_tax(self) -> Decimal:
        return self.cgst_amount + self.sgst_amount + self.igst_amount

    @property
    def has_igst(self) -> bool:
        return self.igst_amount > ZERO

    @property
    def has_intra_state_tax(self) -> bool:
        return self.cgst_amount > ZERO or self.sgst_amount > ZERO


@dataclass(frozen=True)
class Transaction:
    """The engine-side view of one document.

    ``invoice_date`` is :class:`datetime.date` (not str) because the
    Section 16(4) cutoff arithmetic happens against real dates. A
    ``None`` invoice_date short-circuits the time-barred check — the
    verifier will already have flagged the missing date as an issue.
    """

    document_type: str
    vendor: Party
    buyer: Party
    invoice_date: date | None
    line_items: tuple[LineItem, ...] = field(default_factory=tuple)

    @property
    def gross_taxable_value(self) -> Decimal:
        return sum((li.taxable_value for li in self.line_items), start=ZERO)

    @property
    def total_tax(self) -> Decimal:
        return sum((li.total_tax for li in self.line_items), start=ZERO)


# ---------------------------------------------------------------------------
# Extraction → Transaction
# ---------------------------------------------------------------------------


def _safe_decimal(value: object) -> Decimal:
    """Parse a money value, returning :data:`ZERO` on failure.

    The verifier is the place that raises on bad inputs; the engine
    treats unparseable amounts as zero contribution and lets the
    verifier-emitted issues surface to the CA.
    """
    if value is None:
        return ZERO
    try:
        return parse_money(value)
    except (TypeError, ValueError):
        return ZERO


def _party_from(extraction: dict[str, Any], key: str) -> Party:
    block = extraction.get(key) or {}
    if not isinstance(block, dict):
        return Party(gstin=None)
    gstin = block.get("gstin")
    name = block.get("name")
    return Party(
        gstin=gstin.strip().upper() if isinstance(gstin, str) and gstin.strip() else None,
        name=name if isinstance(name, str) else None,
    )


def _line_item_from(raw: dict[str, Any]) -> LineItem:
    return LineItem(
        description=raw.get("description") if isinstance(raw.get("description"), str) else None,
        hsn_sac=raw.get("hsn_sac") if isinstance(raw.get("hsn_sac"), str) else None,
        taxable_value=_safe_decimal(raw.get("taxable_value")),
        cgst_amount=_safe_decimal(raw.get("cgst_amount")),
        sgst_amount=_safe_decimal(raw.get("sgst_amount")),
        igst_amount=_safe_decimal(raw.get("igst_amount")),
    )


def transaction_from_extraction(extraction: dict[str, Any], *, document_type: str) -> Transaction:
    """Project an extraction dict into the engine's typed :class:`Transaction`.

    Pure function. No DB or network. All Decimal parsing happens here so
    every downstream module receives money already quantised.
    """
    line_items_raw = extraction.get("line_items") or []
    line_items: tuple[LineItem, ...] = tuple(
        _line_item_from(item) for item in line_items_raw if isinstance(item, dict)
    )
    return Transaction(
        document_type=document_type,
        vendor=_party_from(extraction, "vendor"),
        buyer=_party_from(extraction, "buyer"),
        invoice_date=parse_iso_date(extraction.get("invoice_date")),
        line_items=line_items,
    )
