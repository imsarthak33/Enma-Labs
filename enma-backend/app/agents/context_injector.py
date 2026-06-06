"""Firm-rule context injection.

Two responsibilities — neither involves an LLM call:

  1. **Engine input**: :func:`load_rules_for_engine` runs the hybrid
     search via :class:`app.db.queries.rules.RuleQuery`, applies the
     deterministic precedence comparator, and returns the
     :class:`ConsultedRule` tuple the tax engine consumes.
  2. **Prompt context**: :func:`format_rules_for_prompt` renders the
     same retrieved set as a short block the Phase 6 supervisor LLM
     prepends to its system message. The renderer is structural —
     enumerated bullets only, never markdown.

The two flows share retrieval so that what the deterministic engine
"sees" and what the LLM "sees" cannot diverge.

Correction recording
--------------------
:func:`record_correction` is a thin wrapper around
``RuleQuery.insert_correction`` that exists in this module so callers
(Phase 6 supervisor tools, admin scripts) have a single import:

    from app.agents.context_injector import record_correction

It performs no NLP — the directive must be supplied already-typed.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tax_engine import ConsultedRule
from app.db.models.rule import CaFirmRule
from app.db.queries.rules import RuleQuery, order_consulted_by_precedence
from app.tax.directives import RuleDirective

__all__ = [
    "format_rules_for_prompt",
    "load_rules_for_engine",
    "record_correction",
]


_TOP_K_DEFAULT: Final[int] = 3


async def load_rules_for_engine(
    *,
    session: AsyncSession,
    ca_firm_id: uuid.UUID,
    client_id: uuid.UUID | None,
    query_text: str,
    limit: int = _TOP_K_DEFAULT,
) -> tuple[ConsultedRule, ...]:
    """Retrieve and order the top-K firm rules for ``query_text``.

    Output is already passed through the precedence comparator, so the
    tax engine can iterate the tuple as-is.
    """
    queries = RuleQuery(session=session, ca_firm_id=ca_firm_id)
    candidates = await queries.search(query_text=query_text, client_id=client_id, limit=limit)
    return order_consulted_by_precedence(candidates)


def format_rules_for_prompt(
    rules: tuple[ConsultedRule, ...], *, rule_texts: dict[uuid.UUID, str]
) -> str:
    """Render ``rules`` as a plain-text block for prompt injection.

    Output is plain, structural text — never markdown. The Phase 6
    supervisor will concatenate this into its system message; the
    formatter intentionally lacks any HTML or markdown so the message
    survives any downstream rendering layer unchanged.

    ``rule_texts`` maps ``rule_id → rule_text`` so the renderer can show
    the English the CA wrote without re-querying. The caller (loader)
    typically builds the map from the search result.
    """
    if not rules:
        return "No firm-specific rules apply."
    lines: list[str] = ["FIRM RULES (highest precedence first):"]
    for idx, rule in enumerate(rules, start=1):
        body = rule_texts.get(rule.rule_id, "(no text)")
        lines.append(f"  {idx}. [{rule.source}] {body}")
        if rule.directive is not None:
            scope_val = rule.directive.scope.value or "client_wide"
            lines.append(
                f"     directive: {rule.directive.action.value} → "
                f"{rule.directive.scope.kind.value}={scope_val}"
            )
    return "\n".join(lines)


async def record_correction(
    *,
    session: AsyncSession,
    ca_firm_id: uuid.UUID,
    client_id: uuid.UUID | None,
    rule_text: str,
    directive: RuleDirective,
) -> CaFirmRule:
    """Persist a human correction. ``directive`` must already be typed.

    The CA's English correction is stored in ``rule_text`` for retrieval;
    the engine reads only ``directive``. There is no LLM parse step —
    callers (Phase 6 supervisor tools, admin scripts, tests) supply the
    directive directly.
    """
    queries = RuleQuery(session=session, ca_firm_id=ca_firm_id)
    return await queries.insert_correction(
        rule_text=rule_text, directive=directive, client_id=client_id
    )
