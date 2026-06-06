"""Slash-command dispatcher — text commands routed deterministically.

Slash commands have to be parsed deterministically because they
mutate state (``/add_client``, ``/assign``) — we do not let an LLM
decide who gets created or assigned. The supervisor agent is for
free-form questions; this module is for the small, well-defined
admin verbs documented in the PRD.

Commands implemented in Phase 6
-------------------------------
* ``/add_client <name> [gstin]`` — Create a new active client.
* ``/list_clients`` — List active clients with id + GSTIN.
* ``/status <client_name>`` — Show client status (open tasks, doc count).
* ``/assign <client_name>`` — Resolve the most recent open
  pending_assignment for the chat to the named client.

Every reply is rendered to Telegram HTML via
:mod:`app.formatting.telegram_html` — no Markdown, no string concat
with user input.
"""

from __future__ import annotations

import shlex
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.queries.clients import ClientQuery
from app.db.queries.pending_assignments import PendingAssignmentQuery
from app.db.queries.tasks import TaskQuery
from app.formatting.telegram_html import bold, code, italic, safe_text
from app.tax.gstin_validator import is_valid_gstin

__all__ = [
    "CommandResult",
    "ParsedCommand",
    "dispatch_command",
    "is_slash_command",
    "parse_command",
]


# ---------------------------------------------------------------------------
# Parse types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedCommand:
    """A successfully tokenised slash command."""

    verb: str
    args: tuple[str, ...]
    raw: str


@dataclass(frozen=True)
class CommandResult:
    """Outcome of executing a slash command."""

    html: str
    success: bool
    pending_assignment_resolved: uuid.UUID | None = None


def is_slash_command(text: str) -> bool:
    """Cheap predicate the worker route uses before calling :func:`parse_command`."""
    return text.startswith("/") and len(text) > 1


def parse_command(text: str) -> ParsedCommand | None:
    """Tokenise a slash command. Returns ``None`` if the verb is unknown.

    Uses :mod:`shlex` posix splitting so quoted multi-word names work:

        /add_client "ABC Industries Pvt Ltd" 27AAACR5055K1Z7
    """
    stripped = text.strip()
    if not is_slash_command(stripped):
        return None
    try:
        tokens = shlex.split(stripped, posix=True)
    except ValueError:
        return None
    if not tokens:
        return None
    verb = tokens[0][1:].lower()
    if verb not in _HANDLERS:
        return None
    return ParsedCommand(verb=verb, args=tuple(tokens[1:]), raw=stripped)


# ---------------------------------------------------------------------------
# Execution context
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ExecCtx:
    session: AsyncSession
    ca_firm_id: uuid.UUID
    chat_id: int


_Handler = Callable[[_ExecCtx, ParsedCommand], Awaitable[CommandResult]]


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------


async def dispatch_command(
    *,
    session: AsyncSession,
    ca_firm_id: uuid.UUID,
    chat_id: int,
    command: ParsedCommand,
) -> CommandResult:
    """Execute a parsed command. The caller has already accepted parsing."""
    handler = _HANDLERS[command.verb]
    ctx = _ExecCtx(session=session, ca_firm_id=ca_firm_id, chat_id=chat_id)
    return await handler(ctx, command)


# ---------------------------------------------------------------------------
# /add_client
# ---------------------------------------------------------------------------


async def _cmd_add_client(ctx: _ExecCtx, cmd: ParsedCommand) -> CommandResult:
    if not cmd.args:
        return _fail(
            "Usage: " + code("/add_client \"Client Name\" [GSTIN]"),
        )
    name = cmd.args[0].strip()
    if not name:
        return _fail("Client name cannot be empty.")
    gstin: str | None = None
    if len(cmd.args) >= 2:
        candidate = cmd.args[1].strip().upper()
        if not is_valid_gstin(candidate):
            return _fail(
                "That doesn't look like a valid GSTIN: " + code(candidate),
            )
        gstin = candidate

    clients = ClientQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    try:
        client = await clients.create(trade_name=name, gstin=gstin)
    except IntegrityError:
        await ctx.session.rollback()
        return _fail(
            "A client with that GSTIN already exists for your firm.",
        )

    line = bold("Client added") + " — " + safe_text(client.trade_name)
    if client.gstin:
        line += " · GSTIN " + code(client.gstin)
    return CommandResult(html=line, success=True)


# ---------------------------------------------------------------------------
# /list_clients
# ---------------------------------------------------------------------------


async def _cmd_list_clients(ctx: _ExecCtx, _cmd: ParsedCommand) -> CommandResult:
    clients = ClientQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    active = list(await clients.list_active())
    if not active:
        return CommandResult(html=italic("No active clients yet."), success=True)
    lines: list[str] = [bold(f"Active clients ({len(active)})")]
    for c in active[:50]:
        gstin_part = " · " + code(c.gstin) if c.gstin else ""
        lines.append("• " + safe_text(c.trade_name) + gstin_part)
    if len(active) > 50:
        lines.append(italic(f"… and {len(active) - 50} more"))
    return CommandResult(html="\n".join(lines), success=True)


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------


async def _cmd_status(ctx: _ExecCtx, cmd: ParsedCommand) -> CommandResult:
    if not cmd.args:
        return _fail("Usage: " + code("/status \"Client Name\""))
    needle = " ".join(cmd.args).strip()
    clients = ClientQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    matches = list(await clients.search_by_name(needle))
    if not matches:
        return _fail("No client matched " + code(needle) + ".")
    if len(matches) > 1:
        ambiguous = "\n".join("• " + safe_text(c.trade_name) for c in matches[:5])
        return _fail(
            bold("Multiple clients matched") + " — please be specific:\n" + ambiguous,
        )

    client = matches[0]
    tasks_q = TaskQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    open_tasks = await tasks_q.list_open(client_id=client.id)

    lines: list[str] = [bold(client.trade_name)]
    if client.gstin:
        lines.append("GSTIN: " + code(client.gstin))
    lines.append(f"Open tasks: {len(open_tasks)}")
    if client.gst_tds_deductor:
        lines.append(italic("GST-TDS deductor (Section 51)"))
    return CommandResult(html="\n".join(lines), success=True)


# ---------------------------------------------------------------------------
# /assign
# ---------------------------------------------------------------------------


async def _cmd_assign(ctx: _ExecCtx, cmd: ParsedCommand) -> CommandResult:
    if not cmd.args:
        return _fail("Usage: " + code("/assign \"Client Name\""))
    needle = " ".join(cmd.args).strip()

    clients = ClientQuery(session=ctx.session, ca_firm_id=ctx.ca_firm_id)
    matches = list(await clients.search_by_name(needle))
    if not matches:
        return _fail("No client matched " + code(needle) + ".")
    if len(matches) > 1:
        ambiguous = "\n".join("• " + safe_text(c.trade_name) for c in matches[:5])
        return _fail(
            bold("Multiple clients matched") + " — please be specific:\n" + ambiguous,
        )
    target = matches[0]

    pending_q = PendingAssignmentQuery(
        session=ctx.session, ca_firm_id=ctx.ca_firm_id
    )
    open_rows = list(await pending_q.list_open_for_chat(chat_id=ctx.chat_id))
    if not open_rows:
        return _fail(
            "No pending document is waiting for assignment in this chat.",
        )
    most_recent = open_rows[0]
    resolved = await pending_q.resolve(
        pending_id=most_recent.id, client_id=target.id
    )
    if resolved is None:
        return _fail(
            "That pending document has already been assigned or expired.",
        )
    html = (
        bold("Assigned")
        + " — document routed to "
        + safe_text(target.trade_name)
        + "."
    )
    return CommandResult(
        html=html,
        success=True,
        pending_assignment_resolved=most_recent.id,
    )


# ---------------------------------------------------------------------------
# Failure helper
# ---------------------------------------------------------------------------


def _fail(html: str) -> CommandResult:
    return CommandResult(html=html, success=False)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


_HANDLERS: Final[dict[str, _Handler]] = {
    "add_client": _cmd_add_client,
    "list_clients": _cmd_list_clients,
    "status": _cmd_status,
    "assign": _cmd_assign,
}
