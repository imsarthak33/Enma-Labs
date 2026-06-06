"""Input Tax Credit — Section 16 eligibility + Section 16(4) time-bar.

This module runs LAST among the sub-engines. By the time it executes,
the blocked sub-engine has already removed any Section 17(5) tax from
the eligible pool, and the RCM sub-engine has computed the recipient's
self-assessed liability separately.

The remaining tax (total tax minus blocked tax) splits into:

  * ``defer_amount`` — when the invoice's filing period differs from
    the current open filing period AND the invoice's own period is
    already **locked** (a row exists in ``filing_approvals`` for it).
    This is the *period_only* defer strategy: deterministic, no
    GSTR-2B dependence. v2 (Phase 7+) layers in supplier compliance.
  * ``block_amount`` — when the invoice date is past the Section 16(4)
    cutoff. The line tax is reclassified as time-barred block (added on
    top of the Section 17(5) block).
  * ``claim_amount`` — everything else.

Reasons carried in the result:
  * ``section_16_eligible`` — claim path taken.
  * ``cross_period_defer`` — defer path taken.
  * ``time_barred`` — Section 16(4) block path taken.
  * ``no_eligible_tax`` — line had zero non-blocked tax.

Pure Python. No LLM. Decimal arithmetic only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.tax.blocked_credits import BlockedResult
from app.tax.types import Transaction
from app.utils.date_utils import (
    FilingPeriod,
    section_16_4_cutoff,
)
from app.utils.decimal_utils import ZERO, quantize_money

__all__ = [
    "ItcResult",
    "PeriodContext",
    "compute_itc",
]


@dataclass(frozen=True)
class PeriodContext:
    """Filing-period state the ITC engine needs from the caller.

    The engine itself does no DB I/O — the pipeline assembles this
    object by querying ``filing_approvals`` before calling
    :func:`compute_itc`.

    ``current`` is the period the firm is currently filing for (open).
    ``locked_periods`` is the set of ``(month, year)`` tuples for which
    a ``filing_approvals`` row exists. Together they implement the
    period_only defer strategy.

    ``as_of`` is the date used for Section 16(4) cutoff comparison —
    defaults to today in IST when the pipeline constructs the context.
    """

    current: FilingPeriod
    locked_periods: frozenset[tuple[int, int]] = field(default_factory=frozenset)
    as_of: date | None = None

    def is_locked(self, period: FilingPeriod) -> bool:
        return period.as_tuple() in self.locked_periods


@dataclass(frozen=True)
class ItcResult:
    """The ITC engine's contribution to the verdict."""

    claim_amount: Decimal
    defer_amount: Decimal
    additional_block_amount: Decimal
    claim_reasons: tuple[str, ...] = field(default_factory=tuple)
    defer_reasons: tuple[str, ...] = field(default_factory=tuple)
    block_reasons: tuple[str, ...] = field(default_factory=tuple)


def compute_itc(
    transaction: Transaction,
    *,
    blocked: BlockedResult,
    period_context: PeriodContext,
) -> ItcResult:
    """Compute claim/defer/time-barred-block from the residual tax pool.

    ``blocked`` carries the Section 17(5) outcome already computed — we
    subtract its per-line ``blocked_tax`` from each line's total tax
    before deciding claim vs defer.
    """
    blocked_by_index: dict[int, Decimal] = {
        outcome.line_index: outcome.blocked_tax for outcome in blocked.per_line
    }

    claim_total: Decimal = ZERO
    defer_total: Decimal = ZERO
    extra_block_total: Decimal = ZERO
    claim_reasons: set[str] = set()
    defer_reasons: set[str] = set()
    block_reasons: set[str] = set()

    invoice_date = transaction.invoice_date
    invoice_period = FilingPeriod.from_date(invoice_date) if invoice_date is not None else None
    as_of = period_context.as_of

    for idx, line in enumerate(transaction.line_items):
        eligible = line.total_tax - blocked_by_index.get(idx, ZERO)
        if eligible <= ZERO:
            continue

        # Section 16(4) time-bar — past the cutoff, the tax becomes a
        # permanent block regardless of the period it sits in.
        if (
            invoice_date is not None
            and as_of is not None
            and as_of > section_16_4_cutoff(invoice_date)
        ):
            extra_block_total += eligible
            block_reasons.add("time_barred")
            continue

        # Cross-period defer (period_only strategy).
        if (
            invoice_period is not None
            and invoice_period != period_context.current
            and period_context.is_locked(invoice_period)
        ):
            defer_total += eligible
            defer_reasons.add("cross_period_defer")
            continue

        claim_total += eligible
        claim_reasons.add("section_16_eligible")

    return ItcResult(
        claim_amount=quantize_money(claim_total),
        defer_amount=quantize_money(defer_total),
        additional_block_amount=quantize_money(extra_block_total),
        claim_reasons=tuple(sorted(claim_reasons)),
        defer_reasons=tuple(sorted(defer_reasons)),
        block_reasons=tuple(sorted(block_reasons)),
    )
