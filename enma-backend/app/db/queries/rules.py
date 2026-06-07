"""Firm-rule CRUD + hybrid (relational + vector) search.

All operations are tenant-scoped via :class:`BaseQuery`. The hybrid
search applies the relational pre-filter first
(``ca_firm_id`` + ``(client_id = X OR client_id IS NULL)`` +
``is_active``), then cosine-similarity-orders the survivors using the
pgvector ``<=>`` operator, and finally hands a candidate set to the
deterministic precedence comparator
:func:`order_consulted_by_precedence`.

Precedence (ADR-005 rev 2 revision #5)
--------------------------------------
1. **Source rank**: ``human_correction`` > ``manual_entry`` > ``system_learned``.
2. **Recency**: newer ``created_at`` wins.
3. **Cosine similarity**: tiebreaker.

The comparator runs in Python after retrieval so all three signals are
under our control — the SQL layer's job is just to narrow the candidate
set efficiently.

Embedding
---------
:meth:`insert_rule` / :meth:`insert_correction` use
:func:`app.services.embedding.embed_text` to generate the vector. The
text-only ``rule_text`` is for retrieval; the typed ``directive`` is
authoritative for the engine.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final, cast

from sqlalchemy import or_, select

from app.agents.tax_engine import ConsultedRule
from app.db.models.rule import CaFirmRule
from app.db.queries.base import BaseQuery
from app.services.embedding import embed_text
from app.tax.directives import RuleDirective

__all__ = [
    "RuleCandidate",
    "RuleQuery",
    "order_consulted_by_precedence",
]


# Source-rank lookup. Lower wins (sorted ascending = best first).
_SOURCE_RANK: Final[dict[str, int]] = {
    "human_correction": 0,
    "manual_entry": 1,
    "system_learned": 2,
}


@dataclass(frozen=True)
class RuleCandidate:
    """One row coming out of the hybrid-search candidate pool.

    Carries everything the precedence comparator and the audit trail
    need, without dragging the ORM model out of the query layer.
    """

    rule_id: uuid.UUID
    source: str
    similarity: Decimal | None
    directive_payload: dict[str, Any]
    rule_text: str
    created_at_epoch: float


class RuleQuery(BaseQuery):
    """Tenant-scoped firm-rule operations."""

    # ----- writes ----------------------------------------------------------

    async def insert_rule(
        self,
        *,
        rule_text: str,
        directive: RuleDirective | None,
        source: str,
        rule_type: str = "preference",
        client_id: uuid.UUID | str | None = None,
    ) -> CaFirmRule:
        """Insert a new firm rule. Generates the embedding for ``rule_text``."""
        if source not in _SOURCE_RANK:
            raise ValueError(f"unknown rule source: {source!r}")
        vector = await embed_text(rule_text, input_type="passage")
        cid: uuid.UUID | None
        if client_id is None:
            cid = None
        elif isinstance(client_id, uuid.UUID):
            cid = client_id
        else:
            cid = uuid.UUID(str(client_id))
        rule = CaFirmRule(
            client_id=cid,
            rule_type=rule_type,
            rule_text=rule_text,
            rule_embedding=vector,
            source=source,
            directive=directive.to_jsonb() if directive is not None else {},
        )
        return cast(CaFirmRule, await self._insert(rule))

    async def insert_correction(
        self,
        *,
        rule_text: str,
        directive: RuleDirective,
        client_id: uuid.UUID | str | None = None,
    ) -> CaFirmRule:
        """Convenience: insert a ``human_correction`` row with a typed directive.

        The directive is REQUIRED here — corrections that don't carry
        a structured override should be inserted as ``manual_entry`` /
        ``note`` rules instead.
        """
        return await self.insert_rule(
            rule_text=rule_text,
            directive=directive,
            source="human_correction",
            rule_type="correction",
            client_id=client_id,
        )

    # ----- reads -----------------------------------------------------------

    async def list_active(self) -> Sequence[CaFirmRule]:
        stmt = self._scoped_select(CaFirmRule).where(CaFirmRule.is_active.is_(True))
        return await self._fetch_all(stmt)

    async def search(
        self,
        *,
        query_text: str,
        client_id: uuid.UUID | str | None = None,
        limit: int = 3,
    ) -> list[RuleCandidate]:
        """Hybrid retrieval: relational pre-filter → cosine ORDER BY → top-K.

        ``client_id`` narrows the candidate pool to rules that are
        either firm-wide (``client_id IS NULL``) or specifically attached
        to this client. The Python-side precedence comparator runs
        afterwards via :func:`order_consulted_by_precedence`.
        """
        cid = (
            client_id
            if client_id is None or isinstance(client_id, uuid.UUID)
            else uuid.UUID(str(client_id))
        )
        vector = await embed_text(query_text, input_type="query")
        stmt = (
            select(
                CaFirmRule.id,
                CaFirmRule.source,
                CaFirmRule.rule_text,
                CaFirmRule.directive,
                CaFirmRule.created_at,
                CaFirmRule.rule_embedding.cosine_distance(vector).label("distance"),
            )
            .where(CaFirmRule.ca_firm_id == self.ca_firm_id)
            .where(CaFirmRule.is_active.is_(True))
        )
        if cid is None:
            stmt = stmt.where(CaFirmRule.client_id.is_(None))
        else:
            stmt = stmt.where(or_(CaFirmRule.client_id == cid, CaFirmRule.client_id.is_(None)))
        stmt = stmt.order_by("distance").limit(limit)
        result = await self.session.execute(stmt)
        rows = result.all()
        candidates: list[RuleCandidate] = []
        for row in rows:
            distance = row.distance
            similarity: Decimal | None = (
                Decimal(str(1.0 - float(distance))) if distance is not None else None
            )
            candidates.append(
                RuleCandidate(
                    rule_id=row.id,
                    source=row.source,
                    similarity=similarity,
                    directive_payload=row.directive or {},
                    rule_text=row.rule_text,
                    created_at_epoch=row.created_at.timestamp(),
                )
            )
        return candidates


# ---------------------------------------------------------------------------
# Precedence comparator
# ---------------------------------------------------------------------------


def order_consulted_by_precedence(
    candidates: Sequence[RuleCandidate],
) -> tuple[ConsultedRule, ...]:
    """Deterministically order ``candidates`` for the tax engine.

    Sort key (ascending = best first):
      1. Source rank (human_correction < manual_entry < system_learned).
      2. Negated ``created_at_epoch`` so newer rules win.
      3. Negated similarity so closer-to-1.0 wins.

    Unknown sources sort last.
    """

    def _key(c: RuleCandidate) -> tuple[int, float, float]:
        rank = _SOURCE_RANK.get(c.source, len(_SOURCE_RANK) + 1)
        sim = float(c.similarity) if c.similarity is not None else 0.0
        return (rank, -c.created_at_epoch, -sim)

    ordered = sorted(candidates, key=_key)
    out: list[ConsultedRule] = []
    for c in ordered:
        directive = RuleDirective.from_jsonb(c.directive_payload)
        out.append(
            ConsultedRule(
                rule_id=c.rule_id,
                source=c.source,
                similarity=c.similarity,
                directive=directive,
                applied=False,
            )
        )
    return tuple(out)
