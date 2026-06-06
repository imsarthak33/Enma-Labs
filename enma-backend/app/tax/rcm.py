"""Reverse Charge Mechanism — Section 9(3) / 9(4) detection + liability.

A reverse-charge trigger fires when:

  * The transaction's ``document_type`` matches a trigger's
    ``document_type_match`` (e.g. FREIGHT → gta_freight), OR
  * Any line's HSN/SAC starts with one of the trigger's prefixes.

GTA carve-out: if the line carries an effective tax rate >= the
trigger's ``forward_charge_rate`` (12%), the supplier has opted into
forward charge and RCM does NOT apply. We compute the effective rate as
``total_tax / taxable_value * 100``.

When RCM applies, the **recipient's** GST liability for the line is the
GST that would otherwise have been collected — for a 5%-tagged GTA
charge that's typically 5% of taxable value. We use the line's already-
extracted tax amounts; if they're zero (because the supplier didn't
collect any), we fall back to applying the trigger's standard rate.

Pure Python. No LLM. Decimal arithmetic only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.prompts.tax_law_library import RCM_TRIGGERS, RcmTrigger
from app.tax.types import LineItem, Transaction
from app.utils.decimal_utils import ZERO, quantize_money

__all__ = [
    "LineRcmOutcome",
    "RcmResult",
    "compute_rcm",
    "line_matches_trigger",
]


# Default RCM rate when the line carries no extracted tax amount AND
# the trigger has no explicit forward-charge rate to invert.
_DEFAULT_RCM_RATE: Decimal = Decimal("5")


@dataclass(frozen=True)
class LineRcmOutcome:
    """Why a single line was (or wasn't) tagged for RCM."""

    line_index: int
    rcm_applies: bool
    rcm_liability: Decimal
    trigger_code: str | None = None
    citation: str | None = None


@dataclass(frozen=True)
class RcmResult:
    """Aggregate of the per-line RCM decisions."""

    rcm_liability: Decimal
    per_line: tuple[LineRcmOutcome, ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)


def line_matches_trigger(line: LineItem, document_type: str) -> RcmTrigger | None:
    """Return the first trigger matching ``line``, or None."""
    for trig in RCM_TRIGGERS:
        if trig.document_type_match and trig.document_type_match == document_type:
            return trig
    hsn = (line.hsn_sac or "").strip()
    if not hsn:
        return None
    for trig in RCM_TRIGGERS:
        for prefix in trig.hsn_prefixes:
            if hsn.startswith(prefix):
                return trig
    return None


def _effective_rate(line: LineItem) -> Decimal:
    if line.taxable_value <= ZERO:
        return ZERO
    return quantize_money(line.total_tax / line.taxable_value * Decimal(100))


def _rcm_liability_for(line: LineItem, trigger: RcmTrigger) -> Decimal:
    """Compute the recipient's RCM liability for ``line``.

    Prefer the line's actually-collected tax (a 5% GTA invoice records
    that 5% on the line — that's the right liability). If the line is
    untaxed (a true RCM invoice often is — supplier collects nothing),
    apply the default RCM rate to ``taxable_value``.
    """
    if line.total_tax > ZERO:
        return quantize_money(line.total_tax)
    if trigger.forward_charge_rate is not None:
        # The trigger has a forward-charge ceiling — assume the standard
        # RCM rate is the one below it (e.g. 5% for GTA when not opted
        # into the 12% forward charge).
        return quantize_money(line.taxable_value * _DEFAULT_RCM_RATE / Decimal(100))
    return quantize_money(line.taxable_value * _DEFAULT_RCM_RATE / Decimal(100))


def compute_rcm(transaction: Transaction) -> RcmResult:
    """Compute the RCM portion of the verdict for ``transaction``."""
    outcomes: list[LineRcmOutcome] = []
    rcm_total: Decimal = ZERO
    reason_codes: set[str] = set()
    for idx, line in enumerate(transaction.line_items):
        trigger = line_matches_trigger(line, transaction.document_type)
        if trigger is None:
            outcomes.append(LineRcmOutcome(line_index=idx, rcm_applies=False, rcm_liability=ZERO))
            continue
        # Forward-charge carve-out: supplier opted in, RCM does not apply.
        if (
            trigger.forward_charge_rate is not None
            and _effective_rate(line) >= trigger.forward_charge_rate
        ):
            outcomes.append(LineRcmOutcome(line_index=idx, rcm_applies=False, rcm_liability=ZERO))
            continue
        liability = _rcm_liability_for(line, trigger)
        outcomes.append(
            LineRcmOutcome(
                line_index=idx,
                rcm_applies=True,
                rcm_liability=liability,
                trigger_code=trigger.code,
                citation=trigger.citation,
            )
        )
        rcm_total += liability
        reason_codes.add(trigger.code)
    return RcmResult(
        rcm_liability=quantize_money(rcm_total),
        per_line=tuple(outcomes),
        reasons=tuple(sorted(reason_codes)),
    )
