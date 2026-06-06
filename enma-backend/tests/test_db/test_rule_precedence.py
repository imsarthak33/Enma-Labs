"""Unit tests for the deterministic rule precedence comparator.

Live hybrid-search behaviour against pgvector lives in
``test_hybrid_search.py`` (integration). This file pins down the
Python-side comparator that decides which candidate's directive wins
when two rules conflict — independent of any DB.

Precedence (ADR-005 rev 2 revision #5):
  1. Source rank: human_correction > manual_entry > system_learned.
  2. Recency: newer wins.
  3. Cosine similarity: closer to 1.0 wins (tiebreaker).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.db.queries.rules import RuleCandidate, order_consulted_by_precedence
from app.tax.directives import (
    DirectiveAction,
    DirectiveScope,
    RuleDirective,
    ScopeKind,
)


def _candidate(
    *,
    source: str,
    similarity: float,
    created_at: datetime,
    directive: RuleDirective | None = None,
) -> RuleCandidate:
    return RuleCandidate(
        rule_id=uuid.uuid4(),
        source=source,
        similarity=Decimal(str(similarity)),
        directive_payload=directive.to_jsonb() if directive else {},
        rule_text="x",
        created_at_epoch=created_at.timestamp(),
    )


_NOTE = RuleDirective(
    action=DirectiveAction.NOTE,
    scope=DirectiveScope(kind=ScopeKind.CLIENT_WIDE),
)


class TestSourceRank:
    def test_human_correction_beats_manual_entry(self) -> None:
        now = datetime.now(tz=UTC)
        human = _candidate(
            source="human_correction", similarity=0.5, created_at=now, directive=_NOTE
        )
        manual = _candidate(source="manual_entry", similarity=0.99, created_at=now, directive=_NOTE)
        ordered = order_consulted_by_precedence([manual, human])
        # human_correction must come first even with the lower similarity.
        assert ordered[0].source == "human_correction"
        assert ordered[1].source == "manual_entry"

    def test_manual_entry_beats_system_learned(self) -> None:
        now = datetime.now(tz=UTC)
        manual = _candidate(source="manual_entry", similarity=0.1, created_at=now, directive=_NOTE)
        system = _candidate(
            source="system_learned", similarity=0.99, created_at=now, directive=_NOTE
        )
        ordered = order_consulted_by_precedence([system, manual])
        assert ordered[0].source == "manual_entry"


class TestRecency:
    def test_newer_wins_within_same_source(self) -> None:
        now = datetime.now(tz=UTC)
        older = _candidate(
            source="human_correction",
            similarity=0.99,
            created_at=now - timedelta(days=30),
            directive=_NOTE,
        )
        newer = _candidate(
            source="human_correction",
            similarity=0.5,
            created_at=now,
            directive=_NOTE,
        )
        ordered = order_consulted_by_precedence([older, newer])
        # Newer wins even though its similarity is lower.
        assert ordered[0].rule_id == newer.rule_id


class TestSimilarityTiebreak:
    def test_higher_similarity_wins_when_source_and_time_tie(self) -> None:
        now = datetime.now(tz=UTC)
        lo = _candidate(source="human_correction", similarity=0.5, created_at=now, directive=_NOTE)
        hi = _candidate(source="human_correction", similarity=0.95, created_at=now, directive=_NOTE)
        ordered = order_consulted_by_precedence([lo, hi])
        assert ordered[0].rule_id == hi.rule_id


class TestDirectiveDecoding:
    def test_typed_directive_recovered(self) -> None:
        now = datetime.now(tz=UTC)
        directive = RuleDirective(
            action=DirectiveAction.FORCE_BLOCK,
            scope=DirectiveScope(kind=ScopeKind.VENDOR_GSTIN, value="27AABCU9603R1ZM"),
        )
        cand = _candidate(
            source="human_correction",
            similarity=0.8,
            created_at=now,
            directive=directive,
        )
        ordered = order_consulted_by_precedence([cand])
        recovered = ordered[0].directive
        assert recovered == directive

    def test_empty_directive_payload_yields_none(self) -> None:
        now = datetime.now(tz=UTC)
        cand = _candidate(
            source="manual_entry",
            similarity=0.8,
            created_at=now,
            directive=None,
        )
        ordered = order_consulted_by_precedence([cand])
        assert ordered[0].directive is None


def test_empty_input_yields_empty_tuple() -> None:
    assert order_consulted_by_precedence([]) == ()
