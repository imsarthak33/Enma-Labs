"""Deterministic tax-verdict orchestrator.

Pure-Python orchestration of the four sub-engines (blocked → rcm → tds →
itc). NO LLM, NO HTTP, NO SENTRY in this file — the CI gate in
``tests/test_tax/test_no_llm_imports.py`` enforces it.

Contract (per ADR-005 rev 2):

  * Inputs: extraction dict, document_type, PeriodContext (locked
    periods + as-of date), gst_tds_deductor flag, ordered firm rules
    (already passed through the precedence comparator by the caller).
  * Output: a :class:`TaxVerdict` whose :meth:`to_jsonb` serialises with
    Decimal-as-strings (never floats), the canonical five outputs,
    structured reasons, the audit trail of consulted rules, an
    ``engine_version``, a ``rule_corpus_hash``, and ``defer_strategy``.
  * Money quantum: ``Decimal('0.01')``, ``ROUND_HALF_UP`` — enforced via
    :mod:`app.utils.decimal_utils`.

Frozen-verdict invariant
------------------------
The engine refuses to compute a verdict for a document whose
``filing_period`` is already locked. The caller (pipeline) is expected
to assemble a :class:`PeriodContext` with the relevant locked set; if
the document's invoice period sits inside the locked set AND the caller
flags ``allow_locked=False`` (the default), the engine raises
:class:`LockedPeriodError`. The pipeline catches this and skips
recompute on already-approved documents.

Rule directives
---------------
:class:`RuleDirective` instances are *advisory inputs* to the engine —
the caller selects which ones to apply via the precedence comparator
(``app.db.queries.rules``). Each consulted rule is recorded in
``firm_rules_consulted`` with ``applied: bool`` so an auditor sees why
one won.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from app.prompts.tax_law_library import EngineModule, modules_for_verdict
from app.tax.blocked_credits import BlockedResult, compute_blocked
from app.tax.directives import DirectiveAction, RuleDirective, ScopeKind
from app.tax.itc import ItcResult, PeriodContext, compute_itc
from app.tax.rcm import RcmResult, compute_rcm
from app.tax.tds import TdsResult, compute_tds
from app.tax.types import LineItem, Transaction, transaction_from_extraction
from app.utils.date_utils import FilingPeriod, now_ist
from app.utils.decimal_utils import ZERO, quantize_money

__all__ = [
    "ConsultedRule",
    "ENGINE_VERSION",
    "LockedPeriodError",
    "TaxVerdict",
    "compute_verdict",
    "directive_applies_to_line",
]


ENGINE_VERSION: Final[str] = "1.0.0"
DEFER_STRATEGY: Final[str] = "period_only"


class LockedPeriodError(RuntimeError):
    """Raised when the engine is asked to recompute a locked-period doc."""


# ---------------------------------------------------------------------------
# Audit-trail row
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConsultedRule:
    """One row of the ``firm_rules_consulted`` audit trail."""

    rule_id: uuid.UUID
    source: str
    similarity: Decimal | None
    directive: RuleDirective | None
    applied: bool
    reason: str | None = None

    def to_jsonb(self) -> dict[str, Any]:
        return {
            "rule_id": str(self.rule_id),
            "source": self.source,
            "similarity": (f"{self.similarity:.4f}" if self.similarity is not None else None),
            "directive": (self.directive.to_jsonb() if self.directive is not None else None),
            "applied": self.applied,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaxVerdict:
    """The engine's full output for one document.

    All Decimals serialise as paise-quantised strings via :meth:`to_jsonb`
    — never as floats and never with extra precision.
    """

    claim_amount: Decimal
    defer_amount: Decimal
    block_amount: Decimal
    rcm_liability: Decimal
    tds_amount: Decimal
    document_type: str
    reasons: dict[str, tuple[str, ...]]
    firm_rules_consulted: tuple[ConsultedRule, ...] = field(default_factory=tuple)
    rule_corpus_hash: str = ""
    computed_at: datetime = field(default_factory=now_ist)
    engine_version: str = ENGINE_VERSION
    defer_strategy: str = DEFER_STRATEGY
    currency: str = "INR"
    # P0 brain substrate: every verdict carries a confidence score. The
    # default 1.0 reflects the deterministic reconciler path — the
    # engine produced the verdict from explicit rules, no probabilistic
    # step involved. Future engine refinements (P9 LoRA, P10 RAG) will
    # write real <1.0 values so the supervisor can threshold-gate
    # auto-execute vs. CA-review.
    confidence: Decimal = field(default_factory=lambda: Decimal("1.0"))

    def to_jsonb(self) -> dict[str, Any]:
        """Serialise to the JSONB shape persisted in ``documents.tax_verdict``."""
        return {
            "claim_amount": f"{self.claim_amount:.2f}",
            "defer_amount": f"{self.defer_amount:.2f}",
            "block_amount": f"{self.block_amount:.2f}",
            "rcm_liability": f"{self.rcm_liability:.2f}",
            "tds_amount": f"{self.tds_amount:.2f}",
            "currency": self.currency,
            "document_type": self.document_type,
            "defer_strategy": self.defer_strategy,
            "reasons": {k: list(v) for k, v in self.reasons.items()},
            "firm_rules_consulted": [r.to_jsonb() for r in self.firm_rules_consulted],
            "engine_version": self.engine_version,
            "rule_corpus_hash": self.rule_corpus_hash,
            "computed_at": self.computed_at.isoformat(),
            "confidence": f"{self.confidence:.4f}",
        }


# ---------------------------------------------------------------------------
# Directive scope matching
# ---------------------------------------------------------------------------


def directive_applies_to_line(
    directive: RuleDirective, *, line: LineItem, transaction: Transaction
) -> bool:
    """Return True when ``directive.scope`` matches the line.

    ``CLIENT_WIDE`` matches every line on every document for the client
    the directive is attached to (the caller is responsible for fetching
    only directives scoped to the right client).
    """
    scope = directive.scope
    if scope.kind is ScopeKind.CLIENT_WIDE:
        return True
    if scope.kind is ScopeKind.DOCUMENT_TYPE:
        return scope.value == transaction.document_type
    if scope.kind is ScopeKind.VENDOR_GSTIN:
        return transaction.vendor.gstin == scope.value
    if scope.kind is ScopeKind.HSN_PREFIX:
        hsn = (line.hsn_sac or "").strip()
        return bool(scope.value) and hsn.startswith(scope.value or "")
    return False  # type: ignore[unreachable]  # exhaustive enum


# ---------------------------------------------------------------------------
# Corpus hash
# ---------------------------------------------------------------------------


def _rule_corpus_hash(rules: tuple[ConsultedRule, ...]) -> str:
    """Stable hash of the consulted-rule set so a verdict can be compared.

    A verdict computed before a new correction lands will have a
    different ``rule_corpus_hash`` from one computed after — even if the
    five-variable output is unchanged. That distinguishability is the
    point: it lets the auditor tell two superficially-identical verdicts
    apart.
    """
    h = hashlib.sha256()
    for r in sorted(rules, key=lambda x: str(x.rule_id)):
        h.update(str(r.rule_id).encode("ascii"))
        h.update(b"|")
        h.update(r.source.encode("ascii"))
        h.update(b"|")
        if r.directive is not None:
            h.update(str(sorted(r.directive.to_jsonb().items())).encode("utf-8"))
        h.update(b"\n")
    return f"sha256:{h.hexdigest()}"


# ---------------------------------------------------------------------------
# Directive application
# ---------------------------------------------------------------------------


@dataclass
class _ApplyState:
    """Mutable accumulator used during the directive pass.

    Lives entirely inside :func:`_apply_directives`; never escapes.
    """

    claim: Decimal
    defer: Decimal
    block: Decimal
    rcm: Decimal
    tds: Decimal
    gst_tds_deductor_override: bool | None
    reasons: dict[str, set[str]]


def _empty_reasons() -> dict[str, set[str]]:
    return {"claim": set(), "block": set(), "rcm": set(), "tds": set(), "defer": set()}


def _apply_directives(
    transaction: Transaction,
    *,
    blocked: BlockedResult,
    rcm: RcmResult,
    itc: ItcResult,
    tds: TdsResult,
    consulted: list[ConsultedRule],
) -> _ApplyState:
    state = _ApplyState(
        claim=itc.claim_amount,
        defer=itc.defer_amount,
        block=blocked.block_amount + itc.additional_block_amount,
        rcm=rcm.rcm_liability,
        tds=tds.tds_amount,
        gst_tds_deductor_override=None,
        reasons=_empty_reasons(),
    )
    state.reasons["claim"].update(itc.claim_reasons)
    state.reasons["defer"].update(itc.defer_reasons)
    state.reasons["block"].update(blocked.reasons)
    state.reasons["block"].update(itc.block_reasons)
    state.reasons["rcm"].update(rcm.reasons)
    if tds.reason is not None:
        state.reasons["tds"].add(tds.reason)

    # The caller has already ordered the rules by precedence; we walk
    # them and rewrite each ConsultedRule with the `applied` flag set.
    for idx, cr in enumerate(consulted):
        directive = cr.directive
        if directive is None:
            continue
        applied = _apply_one_directive(directive, transaction, state)
        consulted[idx] = ConsultedRule(
            rule_id=cr.rule_id,
            source=cr.source,
            similarity=cr.similarity,
            directive=cr.directive,
            applied=applied,
            reason=cr.reason,
        )
    return state


def _apply_one_directive(  # noqa: PLR0911, PLR0912 — exhaustive enum dispatch
    directive: RuleDirective, transaction: Transaction, state: _ApplyState
) -> bool:
    """Apply a directive in-place on ``state``. Returns True if it changed anything."""
    applicable_lines = [
        (idx, line)
        for idx, line in enumerate(transaction.line_items)
        if directive_applies_to_line(directive, line=line, transaction=transaction)
    ]
    if not applicable_lines:
        return False
    total_target_tax = quantize_money(
        sum((line.total_tax for _, line in applicable_lines), start=ZERO)
    )

    if directive.action is DirectiveAction.FORCE_BLOCK:
        if total_target_tax <= ZERO:
            return False
        # Shift the targeted tax out of claim+defer into block. We do
        # this conservatively: cap by what's available in claim+defer.
        shift = min(state.claim + state.defer, total_target_tax)
        if shift <= ZERO:
            return False
        from_claim = min(state.claim, shift)
        from_defer = shift - from_claim
        state.claim = quantize_money(state.claim - from_claim)
        state.defer = quantize_money(state.defer - from_defer)
        state.block = quantize_money(state.block + shift)
        state.reasons["block"].add("firm_rule_force_block")
        return True

    if directive.action is DirectiveAction.FORCE_CLAIM:
        # Pull from defer first, then block. We never invent claim — the
        # source must be existing taxes.
        shift = min(state.defer + state.block, total_target_tax)
        if shift <= ZERO:
            return False
        from_defer = min(state.defer, shift)
        from_block = shift - from_defer
        state.defer = quantize_money(state.defer - from_defer)
        state.block = quantize_money(state.block - from_block)
        state.claim = quantize_money(state.claim + shift)
        state.reasons["claim"].add("firm_rule_force_claim")
        return True

    if directive.action is DirectiveAction.FORCE_DEFER:
        shift = min(state.claim, total_target_tax)
        if shift <= ZERO:
            return False
        state.claim = quantize_money(state.claim - shift)
        state.defer = quantize_money(state.defer + shift)
        state.reasons["defer"].add("firm_rule_force_defer")
        return True

    if directive.action is DirectiveAction.FORCE_RCM:
        # Add RCM liability equal to the targeted tax. The recipient
        # still owes — this directive doesn't remove the original claim
        # path (an LLM-assisted correction would have to be a
        # combination of FORCE_RCM + FORCE_BLOCK if both are intended).
        state.rcm = quantize_money(state.rcm + total_target_tax)
        state.reasons["rcm"].add("firm_rule_force_rcm")
        return True

    if directive.action is DirectiveAction.DISABLE_RCM:
        if state.rcm <= ZERO:
            return False
        state.rcm = ZERO
        state.reasons["rcm"].add("firm_rule_disable_rcm")
        return True

    if directive.action is DirectiveAction.SET_GST_TDS_DEDUCTOR:
        # Pure annotation here — the actual recompute would require
        # rerunning compute_tds with the new flag. The caller is
        # responsible for that on the next document; we record the
        # intent so the audit trail shows the rule was consulted.
        state.gst_tds_deductor_override = True
        state.reasons["tds"].add("firm_rule_set_deductor")
        return True

    if directive.action is DirectiveAction.NOTE:
        # Pure annotation. Records consultation but changes no numbers.
        return False

    return False  # type: ignore[unreachable]  # exhaustive enum


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_verdict(
    extraction: dict[str, Any],
    *,
    document_type: str,
    period_context: PeriodContext,
    gst_tds_deductor: bool = False,
    firm_rules: tuple[ConsultedRule, ...] = (),
    invoice_period: FilingPeriod | None = None,
    allow_locked: bool = False,
) -> TaxVerdict:
    """Run the full tax engine on ``extraction``.

    ``firm_rules`` must already be ordered by the precedence comparator
    (source rank > recency > similarity). The engine walks them in
    order, mutates the running totals deterministically, and records
    each one's ``applied`` flag in the audit trail.

    Raises:
        LockedPeriodError: when ``invoice_period`` is in
            ``period_context.locked_periods`` and ``allow_locked`` is
            False. The pipeline calls this with ``allow_locked=False``
            on recompute to preserve the frozen-verdict invariant.
    """
    if invoice_period is not None and not allow_locked and period_context.is_locked(invoice_period):
        raise LockedPeriodError(f"refusing to recompute verdict for locked period {invoice_period}")

    transaction = transaction_from_extraction(extraction, document_type=document_type)

    active_modules = modules_for_verdict(document_type)
    if not active_modules:
        # B2C / UNKNOWN / unsupported: zeros-only verdict with reason.
        return _zeros_verdict(
            document_type=document_type,
            firm_rules=firm_rules,
            reason="unsupported_document_type",
        )

    blocked = (
        compute_blocked(transaction)
        if EngineModule.BLOCKED in active_modules
        else BlockedResult(block_amount=ZERO)
    )
    rcm = (
        compute_rcm(transaction)
        if EngineModule.RCM in active_modules
        else RcmResult(rcm_liability=ZERO)
    )
    tds = (
        compute_tds(transaction, gst_tds_deductor=gst_tds_deductor)
        if EngineModule.TDS in active_modules
        else TdsResult(tds_amount=ZERO, applied=False)
    )
    itc = (
        compute_itc(transaction, blocked=blocked, period_context=period_context)
        if EngineModule.ITC in active_modules
        else ItcResult(claim_amount=ZERO, defer_amount=ZERO, additional_block_amount=ZERO)
    )

    consulted_mut = list(firm_rules)
    state = _apply_directives(
        transaction, blocked=blocked, rcm=rcm, itc=itc, tds=tds, consulted=consulted_mut
    )
    consulted_final = tuple(consulted_mut)
    return TaxVerdict(
        claim_amount=state.claim,
        defer_amount=state.defer,
        block_amount=state.block,
        rcm_liability=state.rcm,
        tds_amount=state.tds,
        document_type=document_type,
        reasons={k: tuple(sorted(v)) for k, v in state.reasons.items()},
        firm_rules_consulted=consulted_final,
        rule_corpus_hash=_rule_corpus_hash(consulted_final),
    )


def _zeros_verdict(
    *,
    document_type: str,
    firm_rules: tuple[ConsultedRule, ...],
    reason: str,
) -> TaxVerdict:
    """Build a zeros-only verdict for unsupported document types."""
    return TaxVerdict(
        claim_amount=ZERO,
        defer_amount=ZERO,
        block_amount=ZERO,
        rcm_liability=ZERO,
        tds_amount=ZERO,
        document_type=document_type,
        reasons={
            "claim": (),
            "block": (reason,),
            "rcm": (),
            "tds": (),
            "defer": (),
        },
        firm_rules_consulted=firm_rules,
        rule_corpus_hash=_rule_corpus_hash(firm_rules),
    )
