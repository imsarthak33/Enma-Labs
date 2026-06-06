"""Typed firm-rule directives — the structured override surface.

A firm rule has two parallel representations:

* ``rule_text`` — free-form English. Used for embedding + retrieval.
  **Non-authoritative.** The tax engine NEVER reads this.
* ``directive`` — a validated :class:`RuleDirective` object. The
  **authoritative** instruction the tax engine applies deterministically.

Why split them? Free text is great for similarity search but disastrous
for arithmetic correctness — an LLM hallucinating "block ITC" out of
"please don't block this" would corrupt the verdict. Splitting keeps
retrieval flexible and execution deterministic.

How directives are produced
---------------------------
* Phase 5: tests and admin tooling construct :class:`RuleDirective`
  directly.
* Phase 6: the supervisor agent exposes a ``create_firm_rule`` tool
  whose JSON schema **is** :class:`RuleDirective`. The LLM fills a
  constrained tool-call schema; it cannot emit an action outside the
  enum. There is no free-text-to-directive parser — ever.

Persistence
-----------
The directive serialises to ``ca_firm_rules.directive JSONB``. The DB
column carries ``CHECK (jsonb_typeof(directive) = 'object')`` so
non-object payloads are rejected even if some future writer bypasses
Pydantic validation.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "DirectiveAction",
    "DirectiveScope",
    "EMPTY_DIRECTIVE",
    "RuleDirective",
    "ScopeKind",
]


# ---------------------------------------------------------------------------
# Closed enums — Pydantic validates membership.
# ---------------------------------------------------------------------------


class DirectiveAction(StrEnum):
    """The complete set of override actions the tax engine recognises.

    Adding a value here REQUIRES a matching branch in
    :func:`app.agents.tax_engine.apply_directive`. The CI no-LLM gate
    plus the test suite catch the case where the engine forgets a new
    action.
    """

    # ITC outcome forcings.
    FORCE_BLOCK = "force_block"
    FORCE_CLAIM = "force_claim"
    FORCE_DEFER = "force_defer"

    # Reverse charge forcings.
    FORCE_RCM = "force_rcm"
    DISABLE_RCM = "disable_rcm"

    # Per-client static facts that the engine consults but doesn't mutate.
    SET_GST_TDS_DEDUCTOR = "set_gst_tds_deductor"

    # Pure annotation — does not change verdict numbers, but the
    # directive is logged in firm_rules_consulted for audit context.
    NOTE = "note"


class ScopeKind(StrEnum):
    """How a directive's scope matches a candidate document."""

    VENDOR_GSTIN = "vendor_gstin"
    HSN_PREFIX = "hsn_prefix"
    DOCUMENT_TYPE = "document_type"
    CLIENT_WIDE = "client_wide"


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


_MAX_VALUE_LEN: Final[int] = 64


class DirectiveScope(BaseModel):
    """What the directive applies to.

    Exactly one of ``vendor_gstin`` / ``hsn_prefix`` / ``document_type``
    is populated for the targeted ``kind``; ``CLIENT_WIDE`` carries no
    value. The :meth:`_check_value_for_kind` model validator enforces
    the coupling — Pydantic shape alone is not enough.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ScopeKind
    value: str | None = Field(
        default=None,
        max_length=_MAX_VALUE_LEN,
        description="The match target. Must be present for non-CLIENT_WIDE kinds.",
    )

    @model_validator(mode="after")
    def _check_value_for_kind(self) -> DirectiveScope:
        if self.kind is ScopeKind.CLIENT_WIDE:
            if self.value is not None:
                raise ValueError("CLIENT_WIDE scope must not carry a value")
            return self
        if self.value is None or not self.value.strip():
            raise ValueError(f"{self.kind.value} scope requires a value")
        if self.kind is ScopeKind.VENDOR_GSTIN and len(self.value) != 15:
            raise ValueError("vendor_gstin scope must be a 15-char GSTIN")
        if self.kind is ScopeKind.HSN_PREFIX and not self.value.isdigit():
            raise ValueError("hsn_prefix scope must be digits only")
        return self


# ---------------------------------------------------------------------------
# Directive
# ---------------------------------------------------------------------------


class RuleDirective(BaseModel):
    """A typed, deterministic instruction the tax engine consults.

    The engine treats the directive as authoritative; ``rule_text`` is
    retrieval-only and the engine never reads it. Drift between the two
    is a known risk — the integration test suite ensures every
    ``RuleDirective`` carries a non-empty ``rule_text`` at the
    persistence boundary, and reviewers are expected to check that the
    English summary matches the structured action at write time.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: DirectiveAction
    scope: DirectiveScope
    # Free-form note for the audit log. NEVER affects the verdict math;
    # surfaced in firm_rules_consulted only.
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _check_action_scope_compatibility(self) -> RuleDirective:
        """Refuse combinations that the engine cannot honour.

        * ``SET_GST_TDS_DEDUCTOR`` is a per-client static fact — it only
          makes sense at ``CLIENT_WIDE`` scope.
        * Every other action accepts any scope.
        """
        if (
            self.action is DirectiveAction.SET_GST_TDS_DEDUCTOR
            and self.scope.kind is not ScopeKind.CLIENT_WIDE
        ):
            raise ValueError("SET_GST_TDS_DEDUCTOR is only valid at CLIENT_WIDE scope")
        return self

    def to_jsonb(self) -> dict[str, Any]:
        """Serialise to the JSONB shape persisted in ``ca_firm_rules.directive``."""
        return self.model_dump(mode="json")

    @classmethod
    def from_jsonb(cls, payload: dict[str, Any] | None) -> RuleDirective | None:
        """Inverse of :meth:`to_jsonb`. Returns ``None`` for an empty payload.

        An empty JSON object (``{}``) — the default for legacy rows
        inserted before this column existed, or for log-only rules — is
        treated as "no directive", so the engine ignores the row for
        override purposes but the rule is still logged as consulted.
        """
        if not payload:
            return None
        return cls.model_validate(payload)


# Sentinel for "this rule exists but carries no override".
EMPTY_DIRECTIVE: Final[dict[str, Any]] = {}
