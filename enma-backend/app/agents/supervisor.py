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

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.context_injector import format_rules_for_prompt, load_rules_for_engine
from app.db.queries.clients import ClientQuery
from app.db.queries.documents import DocumentQuery
from app.db.queries.filings import FilingQuery
from app.db.queries.tasks import TaskQuery
from app.logging_setup import get_logger
from app.prompts.master_prompt import build_supervisor_prompt
from app.services.llm import (
    ChatMessage,
    ChatResponse,
    LLMError,
    LLMRole,
    call_chat,
)

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
    client_ref = args.get("client_id") or str(ctx.active_client_id or "")
    if not client_ref:
        raise ToolError("client_id is required")
    docs_q = DocumentQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    month = _coerce_int_or_none(args.get("filing_period_month"))
    year = _coerce_int_or_none(args.get("filing_period_year"))
    if month is not None and year is not None:
        rows = await docs_q.list_by_filing_period(year=year, month=month)
        rows = [r for r in rows if str(r.client_id) == str(client_ref)]
    else:
        rows = list(await docs_q.list_by_client(client_id=client_ref))
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
    client_ref = args.get("client_id")
    rows = (
        await tasks_q.list_open(client_id=client_ref)
        if client_ref
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
        client_id=args.get("client_id"),
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


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


TOOLS: Final[dict[str, ToolSpec]] = {
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
    """Render the system prompt with firm-rule context injected."""
    rules = await load_rules_for_engine(
        session=ctx.session,
        ca_firm_id=ctx.ca_firm_id,
        client_id=ctx.active_client_id,
        query_text=user_text,
        limit=3,
    )
    if not rules:
        return build_supervisor_prompt()
    rule_texts = {r.rule_id: "" for r in rules}
    block = format_rules_for_prompt(rules, rule_texts=rule_texts)
    return build_supervisor_prompt(firm_rules_block=block)


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

    for _iteration in range(MAX_TOOL_ITERATIONS):
        try:
            response: ChatResponse = await call_chat(
                LLMRole.REASONING,
                messages,
                temperature=0.0,
                extra_body={"tools": tools, "tool_choice": "auto"},
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
            return SupervisorReply(
                text=text,
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

        for call in tool_calls:
            tool_calls_made += 1
            call_id = cast(str, call.get("id", ""))
            raw_fn = call.get("function")
            fn: dict[str, Any] = raw_fn if isinstance(raw_fn, dict) else {}
            name = cast(str, fn.get("name", ""))
            raw_args = cast(str, fn.get("arguments", "") or "")
            result_json = await _execute_tool(ctx, name, raw_args)
            _log.info(
                "supervisor_tool_called",
                tool=name,
                ca_firm_id=str(ctx.ca_firm_id),
                chat_id=ctx.chat_id,
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": result_json,
                }
            )

    # We exhausted the loop budget — return the last assistant content.
    final_text = messages[-1].get("content") if isinstance(messages[-1].get("content"), str) else ""
    return SupervisorReply(
        text=str(final_text or "I could not finish that request — please try a narrower question."),
        tool_calls_made=tool_calls_made,
        input_tokens=total_input,
        output_tokens=total_output,
    )
