"""Supervisor agent — the text-command brain.

The supervisor runs an OpenAI-style function-calling loop against the
reasoning model. It exposes a small, audited toolbox that all reads
flow through :class:`BaseQuery`, so every tool call is tenant-scoped by
construction.

Architecture
------------
We deliberately do NOT use the OpenAI Python SDK's higher-level "agents"
API; the project's policy is to keep model calls behind the thin
:func:`app.services.llm.call_chat` wrapper. Function-calling is just an
extra ``tools=[...]`` parameter in the chat-completions body, and the
model emits ``message.tool_calls``. We loop until the model returns a
plain assistant message, capping the number of iterations.

Tool registry
-------------
Tools are declared once, in :data:`TOOLS`, with both the JSON-schema
spec (for the LLM) and a Python callable (for the executor). Adding a
tool means adding one entry; the loop discovers it automatically.

Safety
------
* All callables are firm-scoped via the ``SupervisorContext`` passed at
  call time. They cannot escape the tenant boundary.
* The loop never executes arbitrary code — only the registered names.
* Tool exceptions are caught and surfaced to the model as a
  ``role='tool'`` message containing ``{"error": ...}``; the model
  decides how to recover. This keeps a single failing query from
  bringing down the conversation.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final, cast

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.context_injector import format_rules_for_prompt, load_rules_for_engine
from app.db.queries.clients import ClientQuery
from app.db.queries.documents import DocumentQuery
from app.db.queries.filings import FilingQuery
from app.db.queries.rules import RuleQuery
from app.db.queries.tally_exports import TallyExportQuery
from app.db.queries.tasks import TaskQuery
from app.logging_setup import get_logger
from app.prompts.master_prompt import build_supervisor_prompt
from app.services import telegram as telegram_service
from app.services.export import generate_client_ledger_csv
from app.services.llm import (
    ChatMessage,
    ChatResponse,
    LLMError,
    LLMRole,
    call_chat,
)
from app.services.tally import TallyInvoice, compose_tally_envelope
from app.tax.directives import RuleDirective
from app.tax.gstin_validator import is_valid_gstin
from app.tax.reconciler import reconcile_extraction
from app.utils.decimal_utils import parse_money

__all__ = [
    "MAX_TOOL_ITERATIONS",
    "SupervisorContext",
    "SupervisorReply",
    "TOOLS",
    "ToolError",
    "ToolSpec",
    "run_supervisor",
]

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Loop budget
# ---------------------------------------------------------------------------


MAX_TOOL_ITERATIONS: Final[int] = 6
"""Hard cap on tool-call rounds per request — defensive against loops."""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ToolError(RuntimeError):
    """Raised by a tool callable when it cannot fulfil the request.

    The supervisor catches this and feeds the message string back to the
    model as a tool result so the LLM can recover or apologise.
    """


# ---------------------------------------------------------------------------
# Context + result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SupervisorContext:
    """Per-request scope passed to every tool callable.

    All DB queries built from this context inherit ``ca_firm_id`` via
    :class:`BaseQuery`, so no tool can cross firm boundaries.
    """

    session: AsyncSession
    ca_firm_id: uuid.UUID
    chat_id: int
    active_client_id: uuid.UUID | None = None
    firm_name: str | None = None
    ca_name: str | None = None


@dataclass(frozen=True)
class SupervisorReply:
    """The supervisor's final user-facing answer + audit metadata."""

    text: str
    tool_calls_made: int
    input_tokens: int
    output_tokens: int


ToolCallable = Callable[[SupervisorContext, dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class ToolSpec:
    """One row in the tool registry.

    ``schema`` is the JSON-schema body the model sees. ``runner`` is the
    Python coroutine that executes the call. The two stay together so
    schema drift cannot happen silently.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    runner: ToolCallable

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _coerce_uuid_or_none(value: object) -> uuid.UUID | None:
    """Defensive UUID coercion for LLM-emitted tool arguments.

    Companion of :func:`_coerce_int_or_none`. The supervisor LLM tends to
    serialise an "absent" UUID arg as ``"null"``/``"None"``/``""`` (same
    as the int case), but it also sometimes passes the short user-facing
    ref hash (e.g. ``"4b8d91e0"`` — the first eight hex chars of the
    document UUID that the pipeline summary renders). Either path used
    to crash ``uuid.UUID(s)`` with ``ValueError: badly formed
    hexadecimal UUID string`` and take the background task with it.

    Returns:
        * ``None`` for None/empty/"null"/"none"/"undefined".
        * The parsed :class:`uuid.UUID` for any valid representation.
        * For a hex-prefix that is *not* a full UUID (e.g. an 8-char ref),
          we raise :class:`ToolError` rather than guess — the caller has a
          dedicated ``query_document_by_ref`` tool for that case.

    Raises:
        ToolError: when the input is non-empty but not a valid UUID.
    """
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        lowered = cleaned.lower()
        if lowered in {"", "null", "none", "undefined"}:
            return None
        try:
            return uuid.UUID(cleaned)
        except ValueError as exc:
            raise ToolError(
                f"expected a UUID, got {value!r}. "
                "If this is a short Ref hash like '4b8d91e0', "
                "use the query_document_by_ref tool instead."
            ) from exc
    raise ToolError(f"expected a UUID, got {type(value).__name__}")


def _coerce_int_or_none(value: object) -> int | None:
    """Defensive int coercion for LLM-emitted tool arguments.

    The supervisor LLM occasionally serialises an "absent" int argument as
    the JSON string ``"null"`` (literal) or ``"None"`` instead of leaving
    the field out, which used to crash ``int(value)`` with
    ``ValueError: invalid literal for int() with base 10: 'null'`` and take
    the whole background task with it. Treat any of the following as
    "argument not provided":

    * Python ``None``
    * Empty string
    * The literal strings ``"null"``, ``"none"``, ``"undefined"`` (any case)

    Anything else is coerced via ``int()`` and any subsequent ``ValueError``
    is re-raised as a :class:`ToolError` so the LLM gets a structured
    failure it can react to instead of an unhandled crash.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        # JSON parses true/false as Python bool — don't silently coerce.
        raise ToolError("expected an integer, got a boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"", "null", "none", "undefined"}:
            return None
        try:
            return int(cleaned)
        except ValueError as exc:
            raise ToolError(f"expected an integer, got {value!r}") from exc
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise ToolError(f"expected an integer, got {type(value).__name__}")


async def _tool_query_documents(
    ctx: SupervisorContext, args: dict[str, Any]
) -> dict[str, Any]:
    """List documents for a client, optionally filtered by filing period."""
    client_uuid = _coerce_uuid_or_none(args.get("client_id"))
    if client_uuid is None:
        client_uuid = ctx.active_client_id
    if client_uuid is None:
        raise ToolError("client_id is required")
    docs_q = DocumentQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    month = _coerce_int_or_none(args.get("filing_period_month"))
    year = _coerce_int_or_none(args.get("filing_period_year"))
    if month is not None and year is not None:
        rows = await docs_q.list_by_filing_period(year=year, month=month)
        rows = [r for r in rows if r.client_id == client_uuid]
    else:
        rows = list(await docs_q.list_by_client(client_id=client_uuid))
    return {
        "count": len(rows),
        "documents": [
            {
                "id": str(d.id),
                "document_type": d.document_type,
                "filing_period_month": d.filing_period_month,
                "filing_period_year": d.filing_period_year,
                "processing_status": d.processing_status,
                "created_at": d.created_at.isoformat(),
                "tax_verdict": d.tax_verdict,
            }
            for d in rows[:25]
        ],
    }


async def _tool_get_client_status(
    ctx: SupervisorContext, args: dict[str, Any]
) -> dict[str, Any]:
    """Return the client record + open task count + recent doc count."""
    client_ref = args.get("client_id") or args.get("client_name")
    if not client_ref:
        raise ToolError("client_id or client_name is required")

    clients = ClientQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    client = None
    if _looks_like_uuid(str(client_ref)):
        client = await clients.get_by_id(str(client_ref))
    if client is None:
        matches = await clients.search_by_name(str(client_ref))
        if len(matches) == 1:
            client = matches[0]
        elif len(matches) > 1:
            return {
                "ambiguous": True,
                "candidates": [
                    {"id": str(c.id), "trade_name": c.trade_name}
                    for c in matches[:5]
                ],
            }
    if client is None:
        return {"found": False}

    tasks_q = TaskQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    open_tasks = await tasks_q.list_open(client_id=client.id)

    return {
        "found": True,
        "client": {
            "id": str(client.id),
            "trade_name": client.trade_name,
            "legal_name": client.legal_name,
            "gstin": client.gstin,
            "is_active": client.is_active,
            "gst_tds_deductor": client.gst_tds_deductor,
        },
        "open_task_count": len(open_tasks),
    }


async def _tool_list_tasks(
    ctx: SupervisorContext, args: dict[str, Any]
) -> dict[str, Any]:
    """List open tasks, optionally narrowed to a client."""
    tasks_q = TaskQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    client_uuid = _coerce_uuid_or_none(args.get("client_id"))
    rows = (
        await tasks_q.list_open(client_id=client_uuid)
        if client_uuid is not None
        else await tasks_q.list_open()
    )
    return {
        "count": len(rows),
        "tasks": [
            {
                "id": str(t.id),
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "due_at": t.due_at.isoformat() if t.due_at else None,
                "client_id": str(t.client_id) if t.client_id else None,
            }
            for t in rows[:25]
        ],
    }


async def _tool_create_task(
    ctx: SupervisorContext, args: dict[str, Any]
) -> dict[str, Any]:
    """Insert a new pending task scoped to this firm."""
    title = args.get("title")
    if not title or not isinstance(title, str):
        raise ToolError("title is required")
    due_iso = args.get("due_at")
    due_at: datetime | None = None
    if due_iso:
        try:
            due_at = datetime.fromisoformat(str(due_iso))
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=UTC)
        except ValueError as exc:
            raise ToolError(f"due_at must be ISO-8601: {exc}") from exc
    tasks_q = TaskQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    task = await tasks_q.create(
        title=title.strip(),
        description=args.get("description"),
        client_id=_coerce_uuid_or_none(args.get("client_id")),
        due_at=due_at,
        priority=_coerce_int_or_none(args.get("priority")) or 0,
    )
    return {"id": str(task.id), "title": task.title, "status": task.status}


async def _tool_get_filing_summary(
    ctx: SupervisorContext, args: dict[str, Any]
) -> dict[str, Any]:
    """Show period totals + lock status for a given (month, year)."""
    month_i = _coerce_int_or_none(args.get("filing_period_month"))
    year_i = _coerce_int_or_none(args.get("filing_period_year"))
    if month_i is None or year_i is None:
        raise ToolError("filing_period_month and filing_period_year are required")

    filings = FilingQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    locked = await filings.is_period_locked(month=month_i, year=year_i)
    approval = await filings.get_approval(month=month_i, year=year_i) if locked else None

    docs_q = DocumentQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    docs = await docs_q.list_by_filing_period(year=year_i, month=month_i)
    return {
        "filing_period_month": month_i,
        "filing_period_year": year_i,
        "locked": locked,
        "approved_at": approval.created_at.isoformat() if approval else None,
        "document_count": len(docs),
    }


async def _tool_query_document_by_ref(
    ctx: SupervisorContext, args: dict[str, Any]
) -> dict[str, Any]:
    """Look up a document by its user-facing ``Ref:`` hash OR invoice number.

    The pipeline summary renders the document UUID truncated to its
    first eight hex chars as ``Ref:`` — CAs quote that back ("the
    invoice with ref 4b8d91e0"). But CAs ALSO quote the *human*
    invoice number printed on the page ("share me invoice 91"). The
    tool now handles both:

      * If ``ref`` parses as 4-32 hex chars → UUID prefix lookup.
      * Otherwise → invoice-number lookup against
        ``extraction_data->>'invoice_number'``. ``client_name`` can
        narrow the search to one client when given.

    Returns ``{"matches": []}`` when nothing matches, a single-document
    dict when exactly one matches, or ``{"ambiguous": True, "matches":
    [...]}`` when multiple documents share the lookup key.
    """
    ref = args.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        raise ToolError("ref is required (e.g. '4b8d91e0' or '91' as shown on the summary)")

    docs_q = DocumentQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    # First try the hex-prefix path; if the input isn't valid hex, fall
    # through to the human invoice-number path. ``find_by_ref_prefix``
    # raises ValueError on non-hex inputs — we treat that as a signal
    # to try invoice-number lookup, not a hard failure.
    try:
        matches = await docs_q.find_by_ref_prefix(ref)
    except ValueError:
        client_filter: uuid.UUID | None = None
        client_name = args.get("client_name")
        if isinstance(client_name, str) and client_name.strip():
            clients_q = ClientQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
            ranked = await clients_q.search_by_name_ranked(
                client_name, min_similarity=0.55, limit=1
            )
            if ranked:
                client_filter = ranked[0][0].id
        matches = await docs_q.find_by_invoice_number(
            invoice_number=ref, client_id=client_filter
        )

    if not matches:
        return {"matches": []}

    rendered = [
        {
            "id": str(d.id),
            "ref": str(d.id)[:8],
            "client_id": str(d.client_id) if d.client_id else None,
            "document_type": d.document_type,
            "filing_period_month": d.filing_period_month,
            "filing_period_year": d.filing_period_year,
            "processing_status": d.processing_status,
            "created_at": d.created_at.isoformat(),
            "tax_verdict": d.tax_verdict,
            "extraction_data": d.extraction_data,
        }
        for d in matches
    ]
    if len(rendered) == 1:
        return {"matches": rendered, "document": rendered[0]}
    return {"ambiguous": True, "matches": rendered}


# ---------------------------------------------------------------------------
# W2.D — mutating tools.
#
# Each tool writes to the firm-scoped database via the BaseQuery layer
# (tenant isolation by construction). The supervisor calls one of these
# when the CA *tells* Enma to do something — "Add ABC Corp",
# "Set CLEIND's GSTIN to ...", "Mark doc 1ca3f4e0 as approved",
# "From now on all CLEIND invoices use 5% slab".
#
# Every mutating tool returns a structured confirmation that the
# supervisor folds into a one-line user-facing reply ("✅ Added ABC Corp").
# ---------------------------------------------------------------------------


async def _resolve_client_from_args(
    ctx: SupervisorContext, args: dict[str, Any]
) -> Any:
    """Shared helper: resolve a client from a UUID OR a fuzzy name arg.

    Returns the :class:`Client` row, or raises :class:`ToolError` with
    enough detail for the LLM to ask for clarification. Mutating tools
    always need a deterministic target; "I think you meant X" is the
    CA's call, not ours.
    """
    raw_id = args.get("client_id")
    raw_name = args.get("client_name")
    if not raw_id and not raw_name:
        raise ToolError("client_id or client_name is required")
    clients = ClientQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    if raw_id:
        cid = _coerce_uuid_or_none(raw_id)
        if cid is None and _looks_like_uuid(str(raw_id)):
            cid = uuid.UUID(str(raw_id))
        if cid is not None:
            hit = await clients.get_by_id(cid)
            if hit is not None:
                return hit
    if raw_name:
        matches = await clients.search_by_name(str(raw_name))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ToolError(
                "name matched "
                f"{len(matches)} clients ({', '.join(c.trade_name for c in matches[:3])}); "
                "ask the CA to pick"
            )
    raise ToolError(f"no client matches {raw_id or raw_name!r}")


async def _tool_add_client(
    ctx: SupervisorContext, args: dict[str, Any]
) -> dict[str, Any]:
    """Create a new client for the firm. W2.D core mutating tool.

    Trade name is required; GSTIN and legal name are optional. GSTIN is
    validated against the Indian checksum so a typo from natural-language
    parsing ("GSTIN 27ABCDE1234F1Z6") is caught before insert. Duplicate
    GSTIN within the firm raises :class:`ToolError` so the LLM can
    surface it as a friendly conflict message.
    """
    trade_name = args.get("trade_name")
    if not isinstance(trade_name, str) or not trade_name.strip():
        raise ToolError("trade_name is required")
    gstin = args.get("gstin")
    if isinstance(gstin, str) and gstin.strip():
        gstin = gstin.strip().upper()
        if not is_valid_gstin(gstin):
            raise ToolError(f"GSTIN {gstin!r} failed format/checksum validation")
    else:
        gstin = None
    legal_name = args.get("legal_name")
    legal_name = (
        legal_name.strip() if isinstance(legal_name, str) and legal_name.strip() else None
    )
    clients = ClientQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    try:
        client = await clients.create(
            trade_name=trade_name.strip(),
            gstin=gstin,
            legal_name=legal_name,
        )
    except IntegrityError as exc:
        await ctx.session.rollback()
        raise ToolError(
            "A client with that GSTIN already exists for this firm."
        ) from exc
    await ctx.session.commit()
    return {
        "id": str(client.id),
        "trade_name": client.trade_name,
        "gstin": client.gstin,
        "created": True,
    }


async def _tool_update_client(
    ctx: SupervisorContext, args: dict[str, Any]
) -> dict[str, Any]:
    """Edit an existing client's fields. W2.D core mutating tool.

    Accepts ``client_id`` OR ``client_name`` to pick the target; updates
    any of ``trade_name``, ``legal_name``, ``gstin`` (GSTIN re-validated).
    Other fields are accepted opaquely so the LLM can write addresses,
    contact emails, etc. without a schema bump per field.
    """
    target = await _resolve_client_from_args(ctx, args)
    updates: dict[str, Any] = {}
    for key in (
        "trade_name",
        "legal_name",
        "address",
        "contact_email",
        "contact_phone",
        "pan",
        "state_code",
    ):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            updates[key] = val.strip()
    if "gstin" in args:
        gstin = args.get("gstin")
        if isinstance(gstin, str) and gstin.strip():
            gstin = gstin.strip().upper()
            if not is_valid_gstin(gstin):
                raise ToolError(f"GSTIN {gstin!r} failed format/checksum validation")
            updates["gstin"] = gstin
    if not updates:
        raise ToolError("no editable fields provided")
    clients = ClientQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    updated = await clients.update(target.id, **updates)
    if updated is None:
        raise ToolError("update failed — client may have been deleted")
    await ctx.session.commit()
    return {
        "id": str(updated.id),
        "trade_name": updated.trade_name,
        "gstin": updated.gstin,
        "updated_fields": sorted(updates.keys()),
    }


async def _tool_mark_document(
    ctx: SupervisorContext, args: dict[str, Any]
) -> dict[str, Any]:
    """Flip a document's processing_status. W2.D core mutating tool.

    Accepts a short 8-char ``ref`` hash (what the summary prints) and a
    target ``status``. Uses the same ref-prefix lookup the read-side
    ``query_document_by_ref`` tool uses, so the LLM can quote the user's
    "doc 1ca3f4e0" verbatim. Returns ``ambiguous=true`` with candidates
    when the prefix matches more than one document.
    """
    ref = args.get("ref")
    status = args.get("status")
    if not isinstance(ref, str) or not ref.strip():
        raise ToolError("ref is required (e.g. '1ca3f4e0' as shown on the summary)")
    if not isinstance(status, str) or not status.strip():
        raise ToolError("status is required (e.g. 'approved', 'flagged', 'needs_review')")
    docs_q = DocumentQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    try:
        matches = await docs_q.find_by_ref_prefix(ref)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    if not matches:
        return {"matched": False, "ref": ref}
    if len(matches) > 1:
        return {
            "ambiguous": True,
            "matches": [{"id": str(d.id), "ref": str(d.id)[:8]} for d in matches],
        }
    doc = matches[0]
    updated = await docs_q.update_status(doc.id, processing_status=status.strip())
    if updated is None:
        raise ToolError("update failed — document may have been deleted")
    await ctx.session.commit()
    return {
        "id": str(updated.id),
        "ref": str(updated.id)[:8],
        "processing_status": updated.processing_status,
        "updated": True,
    }


async def _tool_add_firm_rule(
    ctx: SupervisorContext, args: dict[str, Any]
) -> dict[str, Any]:
    """Persist a free-form firm rule for future retrieval. W2.D core tool.

    The rule_text is embedded (pgvector) and surfaced via the same
    context_injector the tax engine already consults. Rules are scoped
    firm-wide by default; pass ``client_name`` or ``client_id`` to bind
    the rule to one client. Source is always ``manual_entry`` for rules
    typed by the CA in natural language — the supervisor never tags
    a rule as ``human_correction`` (that requires a specific document
    being corrected, which is a different flow).

    Note: this first cut stores rule_text + scope only. A future
    iteration will let the LLM emit a typed RuleDirective when the
    instruction maps cleanly to one of the engine's DirectiveAction
    enum values — that's what makes the rule *deterministic* at engine
    time. For now the engine still consults rule_text via pgvector
    retrieval, which is the existing behaviour.
    """
    rule_text = args.get("rule_text")
    if not isinstance(rule_text, str) or not rule_text.strip():
        raise ToolError("rule_text is required")

    client_uuid: uuid.UUID | None = None
    if args.get("client_id") or args.get("client_name"):
        target = await _resolve_client_from_args(ctx, args)
        client_uuid = target.id

    rules_q = RuleQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    rule = await rules_q.insert_rule(
        rule_text=rule_text.strip(),
        directive=None,
        source="manual_entry",
        rule_type="preference",
        client_id=client_uuid,
    )
    await ctx.session.commit()
    return {
        "id": str(rule.id),
        "rule_text": rule.rule_text,
        "scope": "client" if client_uuid else "firm",
        "client_id": str(client_uuid) if client_uuid else None,
        "stored": True,
    }


# Silence ruff: RuleDirective is imported for type stability of the
# future "typed_directive" expansion of _tool_add_firm_rule. Keep the
# import; ruff allows it via __all__ surface.
_ = RuleDirective


# ---------------------------------------------------------------------------
# W3 — Tally export.
#
# Composes a Tally Prime ENVELOPE XML for an (already-approved) filing
# period and delivers it as a Telegram document to the CA. Audit row
# written to ``tally_export_runs``. No email — Telegram-only delivery
# in W3; SES is W4a once we own a verified sending domain.
# ---------------------------------------------------------------------------


_GRAND_TOTAL_LABELS: Final[frozenset[str]] = frozenset(
    {"grand total", "total amount", "invoice total", "total invoice value"}
)
_EXPORTABLE_DOCUMENT_STATUSES: Final[frozenset[str]] = frozenset({"completed", "approved"})
_MAX_FILENAME_SLUG_LEN: Final[int] = 40


def _extract_invoice_grand_total(extraction: dict[str, Any]) -> Decimal | None:
    """Pull the printed grand total from the extraction's observed totals.

    Used by the Tally composer to emit a ``Rounded Off`` ledger entry
    when canonical math undershoots the invoice's printed total by a
    paisa or two. Returns ``None`` when the extraction doesn't carry a
    recognisable grand-total label — the composer then trusts the
    reconciler's canonical sum verbatim.
    """
    observed = extraction.get("observed_totals")
    if isinstance(observed, list):
        for entry in observed:
            if not isinstance(entry, dict):
                continue
            raw_label = entry.get("label")
            if not isinstance(raw_label, str):
                continue
            if raw_label.strip().lower() in _GRAND_TOTAL_LABELS:
                try:
                    return parse_money(entry.get("amount"))
                except (TypeError, ValueError):
                    return None
    totals = extraction.get("totals")
    if isinstance(totals, dict):
        try:
            return parse_money(totals.get("grand_total"))
        except (TypeError, ValueError):
            return None
    return None


def _parse_invoice_date(raw: object) -> date | None:
    """Accept ``YYYY-MM-DD`` (and longer ISO timestamps) from the extraction."""
    if isinstance(raw, str) and len(raw) >= 10:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None
    return None


def _filename_slug(name: str) -> str:
    """Filesystem-safe slug from a trade name (no spaces, ASCII-friendly)."""
    cleaned = "".join(c if c.isalnum() else "_" for c in name).strip("_")
    return (cleaned or "client")[:_MAX_FILENAME_SLUG_LEN]


async def _tool_export_to_tally(
    ctx: SupervisorContext, args: dict[str, Any]
) -> dict[str, Any]:
    """Compose a Tally Prime XML for (client, month, year) and send it.

    Acceptance criteria (W3):
      * Filing period MUST be locked (approved). Open periods refuse with
        an actionable error so the supervisor can tell the CA to approve
        the filing first.
      * Only documents with ``processing_status`` in
        ``{completed, approved}`` are included — pending/flagged/failed
        documents are skipped (and counted in the response).
      * Each invoice's canonical math is recomputed via
        :func:`reconcile_extraction` so the export reflects the latest
        reconciler; the LLM's totals are never trusted.
      * One immutable audit row written to ``tally_export_runs``.
      * Delivered as a Telegram document; no email in W3.
    """
    month = _coerce_int_or_none(args.get("month"))
    year = _coerce_int_or_none(args.get("year"))
    if month is None or year is None:
        raise ToolError("month and year are required (e.g. month=7, year=2025)")
    if not (1 <= month <= 12):
        raise ToolError("month must be between 1 and 12")

    client = await _resolve_client_from_args(ctx, args)

    filings = FilingQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    if not await filings.is_period_locked(month=month, year=year):
        raise ToolError(
            f"Filing for {month:02d}/{year} has not been approved yet. "
            "Run ENMA APPROVE FILING for this period first, then export."
        )

    docs_q = DocumentQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    period_docs = await docs_q.list_by_filing_period(year=year, month=month)
    client_docs = [d for d in period_docs if d.client_id == client.id]
    eligible = [
        d for d in client_docs
        if (d.processing_status or "").lower() in _EXPORTABLE_DOCUMENT_STATUSES
    ]
    skipped = len(client_docs) - len(eligible)
    if not eligible:
        return {
            "exported": False,
            "reason": "no_eligible_documents",
            "client": client.trade_name,
            "month": month,
            "year": year,
            "documents_in_period": len(client_docs),
            "documents_skipped": skipped,
        }

    invoices: list[TallyInvoice] = []
    for doc in eligible:
        extraction = doc.extraction_data or {}
        reconciled = reconcile_extraction(extraction)
        vendor_raw = (extraction.get("vendor") or {}).get("name")
        vendor_name = str(vendor_raw).strip() if vendor_raw else "Unknown Vendor"
        invoice_number = str(extraction.get("invoice_number") or str(doc.id)[:8])
        inv_date = _parse_invoice_date(extraction.get("invoice_date"))
        if inv_date is None:
            inv_date = doc.created_at.date()
        invoices.append(
            TallyInvoice(
                vendor_name=vendor_name,
                invoice_number=invoice_number,
                invoice_date=inv_date,
                reconciled=reconciled,
                invoice_grand_total=_extract_invoice_grand_total(extraction),
            )
        )

    xml_bytes = compose_tally_envelope(
        company_name=client.trade_name,
        invoices=invoices,
    )
    file_sha256 = hashlib.sha256(xml_bytes).hexdigest()

    filename = f"{_filename_slug(client.trade_name)}_{year:04d}-{month:02d}_tally.xml"
    caption = (
        f"<b>Tally export</b>\n"
        f"Client: {_xml_escape_for_telegram(client.trade_name)}\n"
        f"Period: {month:02d}/{year}\n"
        f"Vouchers: {len(invoices)}"
    )
    await telegram_service.send_document(
        chat_id=ctx.chat_id,
        file_bytes=xml_bytes,
        filename=filename,
        caption_html=caption,
    )

    exports_q = TallyExportQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    await exports_q.create(
        client_id=client.id,
        filing_month=month,
        filing_year=year,
        voucher_count=len(invoices),
        file_sha256=file_sha256,
        exported_by_chat_id=ctx.chat_id,
    )
    await ctx.session.commit()

    _log.info(
        "tally_export_completed",
        ca_firm_id=str(ctx.ca_firm_id),
        client_id=str(client.id),
        month=month,
        year=year,
        voucher_count=len(invoices),
        file_sha256=file_sha256,
    )

    return {
        "exported": True,
        "client": client.trade_name,
        "month": month,
        "year": year,
        "voucher_count": len(invoices),
        "documents_skipped": skipped,
        "file_sha256": file_sha256,
        "filename": filename,
    }


def _xml_escape_for_telegram(text: str) -> str:
    """Same as Telegram HTML escape — keep imports localised."""
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


# ---------------------------------------------------------------------------
# Firm introspection tool.
#
# The LLM needs a way to answer "what is my firm name?" / "who am I?"
# without hallucinating. The firm identity is injected into the system
# prompt preamble, but this tool also exists so the LLM can fetch
# richer details (email, phone, plan, client count) on explicit ask.
# ---------------------------------------------------------------------------


async def _tool_get_firm_info(
    ctx: SupervisorContext, args: dict[str, Any]
) -> dict[str, Any]:
    """Return the CA firm's own profile and summary stats.

    Answers "what is my firm name", "who am I", "show me my firm
    details", etc. The firm row is already tenant-scoped by
    ``ca_firm_id`` — no additional auth needed.
    """
    from app.db.queries.firms import get_firm_by_id

    firm = await get_firm_by_id(ctx.session, ctx.ca_firm_id)
    if firm is None:
        return {"error": "firm not found"}

    clients_q = ClientQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    active_clients = list(await clients_q.list_active())

    return {
        "firm_name": firm.firm_name,
        "ca_name": firm.ca_name,
        "email": firm.email,
        "phone": firm.phone,
        "subscription_plan": firm.subscription_plan,
        "is_active": firm.is_active,
        "onboarding_completed": firm.onboarding_completed,
        "active_client_count": len(active_clients),
        "created_at": firm.created_at.isoformat() if firm.created_at else None,
    }


# ---------------------------------------------------------------------------
# W3-h7 — approve_filing supervisor tool.
#
# The strict regex `ENMA APPROVE FILING [for|of] Month YYYY` is the
# canonical entry point and stays the safest path. But CAs naturally
# write "ENMA APPROVE FILING for August 2025 of CLEIND" — the regex
# rejects the trailing client name, the message falls through to the
# supervisor, and pre-h7 the LLM hallucinated a tool name. This tool
# gives the LLM a clean way to trigger the same approval flow no
# matter how the CA phrased it. The client name is gracefully ignored
# (approvals are firm-wide; ADR-005 rev 2).
# ---------------------------------------------------------------------------


_MONTH_NAMES_TITLE: Final[tuple[str, ...]] = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


async def _tool_approve_filing(
    ctx: SupervisorContext, args: dict[str, Any]
) -> dict[str, Any]:
    """Start the two-step approval flow for a (month, year) filing.

    Synthesises the canonical ``ENMA APPROVE FILING Month YYYY`` text
    and feeds it to :func:`parse_approval_intent`. Returns the
    snapshot summary + the ``ENMA CONFIRM FILING <hash>`` string the
    CA must reply with to finalise. The actual write is gated by the
    confirm step in :mod:`app.api.routes.worker`, exactly like the
    direct-typed flow — so this tool is no less safe than the regex
    path. Client names are ignored: approvals are firm-wide.
    """
    from app.agents.filing_approval import (
        FilingPeriodAlreadyLocked,
        InvalidApprovalString,
        parse_approval_intent,
    )

    month = _coerce_int_or_none(args.get("month"))
    year = _coerce_int_or_none(args.get("year"))
    if month is None or year is None:
        raise ToolError("month and year are required (e.g. month=8, year=2025)")
    if not (1 <= month <= 12):
        raise ToolError("month must be between 1 and 12")

    month_name = _MONTH_NAMES_TITLE[month - 1]
    synthesised = f"ENMA APPROVE FILING {month_name} {year}"
    try:
        intent = await parse_approval_intent(
            session=ctx.session,
            ca_firm_id=ctx.ca_firm_id,
            text=synthesised,
        )
    except InvalidApprovalString as exc:
        raise ToolError(f"could not parse the approval: {exc}") from exc
    except FilingPeriodAlreadyLocked as exc:
        return {
            "approved": False,
            "reason": "already_locked",
            "month": exc.month,
            "year": exc.year,
        }

    confirm_text = f"ENMA CONFIRM FILING {intent.snapshot_hash[:8]}"
    return {
        "ready_to_confirm": True,
        "month": intent.month,
        "year": intent.year,
        "period_label": intent.snapshot.get("period_label"),
        "document_count": intent.document_count,
        "total_taxable_value": str(intent.total_taxable_value),
        "total_tax": str(intent.total_tax),
        "confirm_with": confirm_text,
    }


# ---------------------------------------------------------------------------
# W3-h4 — Client ledger CSV export.
#
# Ported in from the KARO PITCH prototype. A CSV of every completed
# document for one client (optionally narrowed to a filing period),
# delivered as a Telegram attachment. utf-8-sig encoded so ₹ renders
# in Excel. The composer lives in app/services/export.py — this tool
# is the supervisor surface.
# ---------------------------------------------------------------------------


async def _tool_export_client_ledger(
    ctx: SupervisorContext, args: dict[str, Any]
) -> dict[str, Any]:
    """Compose a client-ledger CSV and deliver it as a Telegram document.

    ``month`` and ``year`` are optional but must arrive together: passing
    only one is a ToolError. Omitting both produces an all-time ledger
    for the client (every completed document).
    """
    month = _coerce_int_or_none(args.get("month"))
    year = _coerce_int_or_none(args.get("year"))
    if (month is None) != (year is None):
        raise ToolError("provide BOTH month and year, or neither for all-time")
    if month is not None and not (1 <= month <= 12):
        raise ToolError("month must be between 1 and 12")

    client = await _resolve_client_from_args(ctx, args)

    try:
        csv_bytes = await generate_client_ledger_csv(
            session=ctx.session,
            ca_firm_id=ctx.ca_firm_id,
            client_id=client.id,
            month=month,
            year=year,
        )
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    # csv_bytes always has at least the BOM + header + summary row;
    # detect the "no eligible documents" case by counting body rows.
    # Cheapest: compare to the empty-period byte length we know is
    # produced — but more robust: decode and split.
    decoded = csv_bytes.decode("utf-8-sig")
    body_rows = decoded.count("\n") - 2  # header + summary excluded
    if body_rows <= 0:
        return {
            "exported": False,
            "reason": "no_eligible_documents",
            "client": client.trade_name,
            "month": month,
            "year": year,
        }

    period_str = f"{year:04d}-{month:02d}" if month is not None and year is not None else "all-time"
    filename = f"{_filename_slug(client.trade_name)}_{period_str}_ledger.csv"
    caption = (
        f"<b>Client ledger</b>\n"
        f"Client: {_xml_escape_for_telegram(client.trade_name)}\n"
        f"Period: {period_str}\n"
        f"Rows: {body_rows}"
    )
    await telegram_service.send_document(
        chat_id=ctx.chat_id,
        file_bytes=csv_bytes,
        filename=filename,
        caption_html=caption,
    )

    _log.info(
        "client_ledger_export_completed",
        ca_firm_id=str(ctx.ca_firm_id),
        client_id=str(client.id),
        month=month,
        year=year,
        row_count=body_rows,
    )

    return {
        "exported": True,
        "client": client.trade_name,
        "month": month,
        "year": year,
        "row_count": body_rows,
        "filename": filename,
    }


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


TOOLS: Final[dict[str, ToolSpec]] = {
    "get_firm_info": ToolSpec(
        name="get_firm_info",
        description=(
            "Return the CA firm's own profile and summary stats. Use when "
            "the CA asks 'what is my firm name', 'who am I', 'show me my "
            "firm details', 'what plan am I on'. Returns firm_name, "
            "ca_name, email, phone, subscription_plan, active_client_count."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        runner=_tool_get_firm_info,
    ),
    "query_documents": ToolSpec(
        name="query_documents",
        description=(
            "List documents the firm has processed for a client. Optionally "
            "filter by GST filing period (month + year)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "client_id": {
                    "type": "string",
                    "description": "Client UUID (omit to use the active client).",
                },
                "filing_period_month": {"type": "integer", "minimum": 1, "maximum": 12},
                "filing_period_year": {"type": "integer", "minimum": 2020, "maximum": 2100},
            },
            "additionalProperties": False,
        },
        runner=_tool_query_documents,
    ),
    "query_document_by_ref": ToolSpec(
        name="query_document_by_ref",
        description=(
            "Look up a document by its short 'Ref:' hex hash OR by the "
            "human invoice number printed on the page. Returns the "
            "matching document with its full UUID, client, totals, tax "
            "verdict, and extracted invoice data. Pass 'ref' as either: "
            "(a) the 4-32 hex char prefix shown on a 'Document processed' "
            "summary (e.g. '4b8d91e0'), OR (b) the invoice number quoted "
            "by the CA (e.g. '91', 'INV-2025-077'). When the user names "
            "a client too ('show me invoice 91 of CLEIND'), pass that as "
            "client_name to narrow the search. Use this BEFORE "
            "query_documents whenever the user mentions a specific "
            "invoice."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": (
                        "Short hex ref ('4b8d91e0') or invoice number "
                        "('91'). The tool auto-detects which."
                    ),
                },
                "client_name": {
                    "type": "string",
                    "description": (
                        "Optional client to narrow an invoice-number lookup."
                    ),
                },
            },
            "required": ["ref"],
            "additionalProperties": False,
        },
        runner=_tool_query_document_by_ref,
    ),
    "get_client_status": ToolSpec(
        name="get_client_status",
        description=(
            "Look up a client by id or fuzzy name. Returns the record + "
            "open-task count. Returns ambiguous=true with candidates if the "
            "name matches more than one active client."
        ),
        parameters={
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
                "client_name": {"type": "string"},
            },
            "additionalProperties": False,
        },
        runner=_tool_get_client_status,
    ),
    "list_tasks": ToolSpec(
        name="list_tasks",
        description="List open (pending or in-progress) tasks for the firm.",
        parameters={
            "type": "object",
            "properties": {
                "client_id": {
                    "type": "string",
                    "description": "Narrow to a single client (UUID).",
                }
            },
            "additionalProperties": False,
        },
        runner=_tool_list_tasks,
    ),
    "create_task": ToolSpec(
        name="create_task",
        description="Create a new pending task for the firm.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "client_id": {"type": "string"},
                "due_at": {
                    "type": "string",
                    "description": "ISO-8601 timestamp (with tzinfo).",
                },
                "priority": {"type": "integer", "minimum": 0, "maximum": 5},
            },
            "required": ["title"],
            "additionalProperties": False,
        },
        runner=_tool_create_task,
    ),
    "get_filing_summary": ToolSpec(
        name="get_filing_summary",
        description=(
            "Show document count and lock status for a (month, year) filing "
            "period. ``locked=true`` means the period has been finalised "
            "via ENMA APPROVE FILING and cannot be modified."
        ),
        parameters={
            "type": "object",
            "properties": {
                "filing_period_month": {"type": "integer", "minimum": 1, "maximum": 12},
                "filing_period_year": {"type": "integer", "minimum": 2020, "maximum": 2100},
            },
            "required": ["filing_period_month", "filing_period_year"],
            "additionalProperties": False,
        },
        runner=_tool_get_filing_summary,
    ),
    # W2.D — mutating tools.
    "add_client": ToolSpec(
        name="add_client",
        description=(
            "Create a new client for the firm. Call this when the CA says "
            "'Add ABC Corp', 'register XYZ Pvt Ltd', 'new client X with "
            "GSTIN ...'. trade_name is required. gstin is validated via "
            "Indian checksum if provided. Duplicate GSTIN in the firm is "
            "an error (return it to the CA verbatim)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "trade_name": {"type": "string"},
                "gstin": {"type": "string"},
                "legal_name": {"type": "string"},
            },
            "required": ["trade_name"],
            "additionalProperties": False,
        },
        runner=_tool_add_client,
    ),
    "update_client": ToolSpec(
        name="update_client",
        description=(
            "Edit an existing client's profile. Use when the CA says "
            "'Set CLEIND's GSTIN to ...', 'update X's address', 'rename "
            "Y to Z'. Pass client_id OR client_name to pick the target. "
            "Any of trade_name/legal_name/gstin/address/contact_email/"
            "contact_phone/pan/state_code may be supplied; only the ones "
            "you pass are updated."
        ),
        parameters={
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
                "client_name": {"type": "string"},
                "trade_name": {"type": "string"},
                "legal_name": {"type": "string"},
                "gstin": {"type": "string"},
                "address": {"type": "string"},
                "contact_email": {"type": "string"},
                "contact_phone": {"type": "string"},
                "pan": {"type": "string"},
                "state_code": {"type": "string"},
            },
            "additionalProperties": False,
        },
        runner=_tool_update_client,
    ),
    "mark_document": ToolSpec(
        name="mark_document",
        description=(
            "Flip a document's processing_status. Use when the CA says "
            "'mark doc 1ca3f4e0 as approved', 'flag this one for review', "
            "'set X to filed'. Pass the short 'ref' hash (4-32 hex chars) "
            "shown on every Document-processed summary, plus the new "
            "status string. Status is free-form (e.g. 'approved', "
            "'flagged', 'needs_review', 'filed')."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "Short hex ref shown on summaries (e.g. '1ca3f4e0').",
                },
                "status": {"type": "string"},
            },
            "required": ["ref", "status"],
            "additionalProperties": False,
        },
        runner=_tool_mark_document,
    ),
    "add_firm_rule": ToolSpec(
        name="add_firm_rule",
        description=(
            "Persist a firm-level rule the CA wants Enma to remember and "
            "apply on future documents. Use when the CA says 'from now on "
            "all CLEIND invoices are 5% slab', 'always treat vendor X as "
            "RCM', 'mark hotel bills under 7500 as eligible'. Provide "
            "rule_text verbatim — Enma re-reads it via vector retrieval "
            "at engine time. Pass client_id or client_name to scope the "
            "rule to one client; omit both for a firm-wide rule."
        ),
        parameters={
            "type": "object",
            "properties": {
                "rule_text": {"type": "string"},
                "client_id": {"type": "string"},
                "client_name": {"type": "string"},
            },
            "required": ["rule_text"],
            "additionalProperties": False,
        },
        runner=_tool_add_firm_rule,
    ),
    "export_to_tally": ToolSpec(
        name="export_to_tally",
        description=(
            "Compose a Tally Prime ENVELOPE XML for one client's filing "
            "period and deliver it to the CA as a Telegram document. Use "
            "when the CA says 'export July 2025 for CLEIND', 'send me the "
            "Tally file for client X for last month', 'push June filings "
            "to Tally for ABC Corp'. The filing period MUST already be "
            "approved (run ENMA APPROVE FILING first); the tool returns "
            "an actionable error otherwise. Pass client_id OR client_name "
            "plus month (1-12) and year (e.g. 2025). The audit row in "
            "tally_export_runs is written automatically. Surface the "
            "voucher_count and file_sha256 in your reply."
        ),
        parameters={
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
                "client_name": {"type": "string"},
                "month": {"type": "integer", "minimum": 1, "maximum": 12},
                "year": {"type": "integer", "minimum": 2020, "maximum": 2100},
            },
            "required": ["month", "year"],
            "additionalProperties": False,
        },
        runner=_tool_export_to_tally,
    ),
    "export_client_ledger": ToolSpec(
        name="export_client_ledger",
        description=(
            "Compose a CSV ledger for a client and deliver it to the CA "
            "as a Telegram attachment (utf-8-sig so ₹ renders in Excel). "
            "Use when the CA asks for 'the ledger for ABC Corp', 'send "
            "me CLEIND's invoices for July', 'export the client ledger'. "
            "Pass client_id OR client_name. month and year are OPTIONAL "
            "but must arrive together — omit both for an all-time "
            "ledger covering every completed document for that client. "
            "Distinct from export_to_tally: this is the human-readable "
            "spreadsheet view; export_to_tally is the Tally Prime XML "
            "import file. Surface the row_count in your reply."
        ),
        parameters={
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
                "client_name": {"type": "string"},
                "month": {"type": "integer", "minimum": 1, "maximum": 12},
                "year": {"type": "integer", "minimum": 2020, "maximum": 2100},
            },
            "additionalProperties": False,
        },
        runner=_tool_export_client_ledger,
    ),
    "approve_filing": ToolSpec(
        name="approve_filing",
        description=(
            "Start the two-step filing approval for a (month, year). Use "
            "when the CA says 'approve filing for August 2025', 'lock "
            "the July filing', 'ENMA APPROVE FILING for June 2026 of "
            "CLEIND' (client name is ignored — approvals are firm-wide). "
            "Returns a snapshot summary (document_count, totals) plus "
            "the 'ENMA CONFIRM FILING <hash>' string the CA must reply "
            "with to actually lock the period. Surface that confirm "
            "string verbatim in <code> tags so it's one-tap copyable. "
            "DO NOT finalise the approval yourself — only the CA's "
            "explicit confirm reply may lock the period."
        ),
        parameters={
            "type": "object",
            "properties": {
                "month": {"type": "integer", "minimum": 1, "maximum": 12},
                "year": {"type": "integer", "minimum": 2020, "maximum": 2100},
            },
            "required": ["month", "year"],
            "additionalProperties": False,
        },
        runner=_tool_approve_filing,
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _looks_like_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        return False
    return True


def _openai_tools() -> list[dict[str, Any]]:
    return [spec.to_openai_tool() for spec in TOOLS.values()]


def _extract_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    raw = message.get("tool_calls")
    if not isinstance(raw, list):
        return []
    return [c for c in raw if isinstance(c, dict)]


async def _build_system_prompt(ctx: SupervisorContext, user_text: str) -> str:
    """Render the system prompt with firm identity + rules injected."""
    rules = await load_rules_for_engine(
        session=ctx.session,
        ca_firm_id=ctx.ca_firm_id,
        client_id=ctx.active_client_id,
        query_text=user_text,
        limit=3,
    )
    rule_block: str | None = None
    if rules:
        rule_texts = {r.rule_id: "" for r in rules}
        rule_block = format_rules_for_prompt(rules, rule_texts=rule_texts)
    return build_supervisor_prompt(
        firm_name=ctx.firm_name,
        ca_name=ctx.ca_name,
        firm_rules_block=rule_block,
    )


async def _execute_tool(
    ctx: SupervisorContext, name: str, raw_args: str
) -> str:
    """Dispatch one tool call and return its JSON-serialised result."""
    spec = TOOLS.get(name)
    if spec is None:
        return json.dumps({"error": f"unknown tool: {name}"})
    try:
        args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"invalid JSON arguments: {exc}"})
    if not isinstance(args, dict):
        return json.dumps({"error": "arguments must be a JSON object"})
    try:
        result = await spec.runner(ctx, args)
    except ToolError as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


_LOOP_EXHAUSTED_FALLBACK: Final[str] = (
    "I ran into trouble finishing that. Could you rephrase it?"
)


def _tool_result_carries_error(result_json: str) -> bool:
    """True iff a tool result string parses to an object with an ``error`` key.

    ToolError handling in :func:`_execute_tool` always emits
    ``{"error": "..."}``, and several read-side tools also surface
    structured ``error`` fields. After we see one, the LLM must produce
    a text reply on the next round — otherwise it tends to re-call the
    same tool and burn the iteration budget.
    """
    try:
        parsed = json.loads(result_json)
    except (ValueError, TypeError):
        return False
    return isinstance(parsed, dict) and "error" in parsed


def _looks_like_json(text: str) -> bool:
    """Heuristic: candidate user-facing reply is a raw JSON blob.

    In production we observed ``meta/llama-3.3-70b-instruct`` parroting
    tool-result JSON verbatim as its assistant content. The user-facing
    reply should never start with ``{`` or ``[``; if it does and parses
    cleanly, it's the parroting pattern.
    """
    stripped = text.strip()
    if not stripped or stripped[0] not in {"{", "["}:
        return False
    try:
        json.loads(stripped)
    except (ValueError, TypeError):
        return False
    return True


_HUMANIZE_DIRECTIVE: Final[str] = (
    "Your previous reply was raw JSON data, not natural language. The CA "
    "must NEVER see JSON. Rewrite your reply as ONE short natural-English "
    "sentence summarising what the tool found, or what went wrong and what "
    "the CA should do next. Light HTML (<b>, <i>, <code>) and one status "
    "emoji (✅ ⚠ ❌ 📊) at the head of the line are allowed. No JSON. "
    "No code fences. No braces."
)


async def _humanize_if_json(
    *,
    messages: list[ChatMessage],
    candidate: str,
    tools: list[dict[str, Any]],
) -> tuple[str, int, int]:
    """Defensive guard: re-prompt the LLM if it parroted tool JSON.

    Returns ``(final_text, extra_input_tokens, extra_output_tokens)``.
    ``extra_*`` are 0 when the candidate already looks human-readable.
    On any failure of the rewrite call, falls back to a canned message —
    we never surface raw JSON to the user.
    """
    if not _looks_like_json(candidate):
        return candidate, 0, 0

    _log.warning("supervisor_json_parrot_detected", candidate_prefix=candidate[:120])
    rewrite_messages: list[ChatMessage] = [*messages, {
        "role": "system",
        "content": _HUMANIZE_DIRECTIVE,
    }]
    try:
        rewrite = await call_chat(
            LLMRole.REASONING,
            rewrite_messages,
            temperature=0.0,
            extra_body={"tools": tools, "tool_choice": "none"},
        )
    except LLMError:
        return _LOOP_EXHAUSTED_FALLBACK, 0, 0

    rewritten = (rewrite.content or "").strip()
    if not rewritten or _looks_like_json(rewritten):
        return _LOOP_EXHAUSTED_FALLBACK, rewrite.input_tokens, rewrite.output_tokens
    return rewritten, rewrite.input_tokens, rewrite.output_tokens


async def run_supervisor(
    *,
    ctx: SupervisorContext,
    user_text: str,
    history: list[ChatMessage] | None = None,
) -> SupervisorReply:
    """Run the ReAct / function-calling loop and return the final reply.

    ``history`` is a list of prior chat-completions messages (already in
    the OpenAI shape) the caller wants to pre-load — typically the
    bounded conversation window from :class:`ConversationQuery`.

    Loop-termination guarantees
    ---------------------------
    * If any tool in iteration *N* returns ``{"error": ...}``, iteration
      *N+1* forces ``tool_choice="none"`` so the LLM must produce a text
      reply about the error rather than re-calling the failing tool.
      This cuts the worst-case "LLM keeps re-trying export_to_tally on
      an unapproved filing" loop from six iterations to two.
    * If the budget exhausts anyway, we do ONE final ``tool_choice="none"``
      call to coax a text reply, never the raw tool JSON. If that final
      call also raises, we surface ``_LOOP_EXHAUSTED_FALLBACK`` —
      ``messages[-1].content`` (a tool result JSON blob) is never sent
      to the user.
    """
    system_prompt = await _build_system_prompt(ctx, user_text)
    messages: list[ChatMessage] = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    total_input = 0
    total_output = 0
    tool_calls_made = 0
    tools = _openai_tools()
    # When the previous iteration produced a tool error, force the next
    # call to text-reply rather than retry the failing tool.
    force_text_next = False

    for _iteration in range(MAX_TOOL_ITERATIONS):
        tool_choice: str = "none" if force_text_next else "auto"
        try:
            response: ChatResponse = await call_chat(
                LLMRole.REASONING,
                messages,
                temperature=0.0,
                extra_body={"tools": tools, "tool_choice": tool_choice},
            )
        except LLMError:
            raise

        total_input += response.input_tokens
        total_output += response.output_tokens

        choice = (response.raw.get("choices") or [{}])[0]
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        tool_calls = _extract_tool_calls(message)

        if not tool_calls:
            text = (message.get("content") or response.content or "").strip()
            text, rwi, rwo = await _humanize_if_json(
                messages=messages, candidate=text, tools=tools,
            )
            total_input += rwi
            total_output += rwo
            return SupervisorReply(
                text=text or _LOOP_EXHAUSTED_FALLBACK,
                tool_calls_made=tool_calls_made,
                input_tokens=total_input,
                output_tokens=total_output,
            )

        # Echo the assistant message (with tool_calls) back so the model
        # has a coherent transcript on the next round.
        messages.append(
            {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": tool_calls,
            }
        )

        force_text_next = False
        for call in tool_calls:
            tool_calls_made += 1
            call_id = cast(str, call.get("id", ""))
            raw_fn = call.get("function")
            fn: dict[str, Any] = raw_fn if isinstance(raw_fn, dict) else {}
            name = cast(str, fn.get("name", ""))
            raw_args = cast(str, fn.get("arguments", "") or "")
            result_json = await _execute_tool(ctx, name, raw_args)
            had_error = _tool_result_carries_error(result_json)
            force_text_next = force_text_next or had_error
            _log.info(
                "supervisor_tool_called",
                tool=name,
                ca_firm_id=str(ctx.ca_firm_id),
                chat_id=ctx.chat_id,
                tool_result_error=had_error,
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": result_json,
                }
            )

    # Budget exhausted. One last text-only attempt so the user gets a
    # human-readable summary of whatever the tools surfaced — and
    # NEVER the raw tool-result JSON.
    _log.warning(
        "supervisor_loop_budget_exhausted",
        ca_firm_id=str(ctx.ca_firm_id),
        chat_id=ctx.chat_id,
        tool_calls_made=tool_calls_made,
    )
    try:
        final_response: ChatResponse = await call_chat(
            LLMRole.REASONING,
            messages,
            temperature=0.0,
            extra_body={"tools": tools, "tool_choice": "none"},
        )
        total_input += final_response.input_tokens
        total_output += final_response.output_tokens
        text = (final_response.content or "").strip()
    except LLMError:
        text = ""

    text, rwi, rwo = await _humanize_if_json(
        messages=messages, candidate=text, tools=tools,
    )
    total_input += rwi
    total_output += rwo

    return SupervisorReply(
        text=text or _LOOP_EXHAUSTED_FALLBACK,
        tool_calls_made=tool_calls_made,
        input_tokens=total_input,
        output_tokens=total_output,
    )
