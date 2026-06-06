"""Conversation log queries + sliding-window compaction.

Two responsibilities — both tenant-scoped via :class:`BaseQuery`:

1. **Turn persistence**: every user / assistant / system message turn
   is appended to ``conversations``. The supervisor reads back a
   bounded window before each LLM call.

2. **Compaction**: when the live window grows past the configured
   trigger (turns OR estimated tokens), the oldest 50% of turns are
   summarised by the summarisation LLM and replaced with a single
   ``role='system', metadata={'kind': 'summary'}`` row. The original
   turns are tombstoned with ``metadata['compacted_into'] = <summary_id>``
   but never deleted — the table doubles as the immutable audit log.

The compaction uses :func:`app.services.llm.call_chat` with the
reasoning role and a fixed prompt; the prompt lives in this module to
keep the summarisation behaviour reviewable in one file (per the
single-source-of-truth routing rule in the backend doc).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, cast

from sqlalchemy import update

from app.db.models.conversation import Conversation
from app.db.queries.base import BaseQuery
from app.logging_setup import get_logger
from app.services.llm import (
    ChatMessage,
    LLMError,
    LLMRole,
    call_chat,
)

__all__ = [
    "COMPACT_TRIGGER_TOKENS",
    "COMPACT_TRIGGER_TURNS",
    "COMPACT_WINDOW_TURNS",
    "CompactionResult",
    "ConversationQuery",
    "RECENT_WINDOW_TURNS",
    "estimate_tokens",
]

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Tuning constants — module-level so tests can patch them.
# ---------------------------------------------------------------------------

COMPACT_TRIGGER_TURNS: Final[int] = 20
"""Compaction fires once an open window exceeds this many live turns."""

COMPACT_TRIGGER_TOKENS: Final[int] = 8000
"""Compaction also fires above this estimated input-token count."""

COMPACT_WINDOW_TURNS: Final[int] = 50
"""Hard ceiling: we never look at more turns than this when deciding."""

RECENT_WINDOW_TURNS: Final[int] = 10
"""Default size of the window the supervisor reads for context."""

# ---------------------------------------------------------------------------
# Roles + metadata keys
# ---------------------------------------------------------------------------

_ROLE_USER: Final[str] = "user"
_ROLE_ASSISTANT: Final[str] = "assistant"
_ROLE_SYSTEM: Final[str] = "system"

_META_KIND: Final[str] = "kind"
_META_SUMMARY: Final[str] = "summary"
_META_COMPACTED_INTO: Final[str] = "compacted_into"

_ALLOWED_ROLES: Final[frozenset[str]] = frozenset(
    {_ROLE_USER, _ROLE_ASSISTANT, _ROLE_SYSTEM}
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompactionResult:
    """Outcome of a single ``maybe_compact`` call."""

    compacted: bool
    summary_id: uuid.UUID | None
    turns_compacted: int
    remaining_turns: int


# ---------------------------------------------------------------------------
# Token estimator
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Cheap, deterministic token estimate.

    Uses the GPT rule-of-thumb (~4 chars per token) — good enough for
    a compaction trigger heuristic. We intentionally do NOT pull in a
    real BPE tokenizer; the cost is not worth it for an "are we
    over 8k?" gate.
    """
    if not text:
        return 0
    # Round up so a 3-char message still scores as 1 token.
    return max(1, (len(text) + 3) // 4)


# ---------------------------------------------------------------------------
# Summarisation prompt
# ---------------------------------------------------------------------------


_SUMMARISATION_SYSTEM: Final[str] = (
    "You are a faithful summariser for a Chartered Accountancy workflow "
    "assistant. Compress the user/assistant turns below into a single "
    "structured digest preserving: (a) every client name mentioned, "
    "(b) every concrete decision made or pending, (c) any GSTIN, PAN, "
    "invoice number, or amount that appears. Do not invent facts. "
    "Do not include opinions. Do not use markdown. Plain prose only, "
    "≤ 200 words."
)


def _build_summarisation_messages(turns: Sequence[Conversation]) -> list[ChatMessage]:
    """Render turns into a chat-completions message list for the summariser."""
    transcript_lines: list[str] = []
    for turn in turns:
        role_label = turn.role.upper()
        transcript_lines.append(f"[{role_label}] {turn.content}")
    transcript = "\n".join(transcript_lines)
    return [
        {"role": "system", "content": _SUMMARISATION_SYSTEM},
        {
            "role": "user",
            "content": (
                "Summarise the following CA-firm conversation excerpt:\n\n"
                f"{transcript}"
            ),
        },
    ]


# ---------------------------------------------------------------------------
# Query class
# ---------------------------------------------------------------------------


class ConversationQuery(BaseQuery):
    """Tenant-scoped conversation log operations."""

    # ----- writes ----------------------------------------------------------

    async def append_turn(
        self,
        *,
        chat_id: int,
        role: str,
        content: str,
        client_id: uuid.UUID | str | None = None,
        message_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Conversation:
        """Append a single turn to the firm's conversation log.

        ``role`` must be one of user/assistant/system. Pass ``client_id``
        when the turn is tied to a specific client (drives the
        identity-resolver stage 1 cache).
        """
        if role not in _ALLOWED_ROLES:
            raise ValueError(f"unknown conversation role: {role!r}")
        cid: uuid.UUID | None
        if client_id is None:
            cid = None
        elif isinstance(client_id, uuid.UUID):
            cid = client_id
        else:
            cid = uuid.UUID(str(client_id))
        turn = Conversation(
            chat_id=chat_id,
            client_id=cid,
            role=role,
            content=content,
            message_id=message_id,
            metadata_=metadata or {},
        )
        return cast(Conversation, await self._insert(turn))

    # ----- reads -----------------------------------------------------------

    async def list_recent_live(
        self,
        *,
        chat_id: int,
        limit: int = RECENT_WINDOW_TURNS,
    ) -> list[Conversation]:
        """Return up to ``limit`` most recent *live* turns, oldest first.

        A turn is *live* iff its metadata does not carry
        ``compacted_into`` — i.e. it has not been replaced by a summary.
        """
        stmt = (
            self._scoped_select(Conversation)
            .where(Conversation.chat_id == chat_id)
            .where(
                ~Conversation.metadata_["compacted_into"].astext.is_not(None)
            )
            .order_by(Conversation.created_at.desc())
            .limit(limit)
        )
        rows = await self._fetch_all(stmt)
        return list(reversed(list(rows)))

    async def get_last_client_id(
        self, *, chat_id: int, limit: int = 3
    ) -> uuid.UUID | None:
        """Return the most recent ``client_id`` seen in the last ``limit`` turns."""
        stmt = (
            self._scoped_select(Conversation)
            .where(Conversation.chat_id == chat_id)
            .where(Conversation.client_id.is_not(None))
            .order_by(Conversation.created_at.desc())
            .limit(limit)
        )
        rows = await self._fetch_all(stmt)
        for row in rows:
            if row.client_id is not None:
                return row.client_id
        return None

    # ----- compaction ------------------------------------------------------

    async def maybe_compact(self, *, chat_id: int) -> CompactionResult:
        """Compact the oldest 50% of the live window if the triggers fire.

        Trigger: live-turn count > ``COMPACT_TRIGGER_TURNS`` OR estimated
        input-tokens for the live window > ``COMPACT_TRIGGER_TOKENS``.

        When compaction succeeds, a single ``role='system'`` summary row
        is inserted with ``metadata={'kind': 'summary'}`` and the
        compacted source rows are tombstoned with
        ``metadata['compacted_into'] = <summary_id>``.
        """
        live = await self._list_live_window(chat_id=chat_id, limit=COMPACT_WINDOW_TURNS)
        turn_count = len(live)
        total_tokens = sum(estimate_tokens(turn.content) for turn in live)

        if (
            turn_count <= COMPACT_TRIGGER_TURNS
            and total_tokens <= COMPACT_TRIGGER_TOKENS
        ):
            return CompactionResult(
                compacted=False,
                summary_id=None,
                turns_compacted=0,
                remaining_turns=turn_count,
            )

        # Compact the older half.
        half = max(1, turn_count // 2)
        oldest = live[:half]

        try:
            summary_text = await self._summarise(oldest)
        except LLMError as exc:
            # Compaction is best-effort. Don't block the live conversation
            # on a summariser outage; the next attempt will retry.
            _log.warning("conversation_compaction_failed", error=str(exc))
            return CompactionResult(
                compacted=False,
                summary_id=None,
                turns_compacted=0,
                remaining_turns=turn_count,
            )

        # Insert the summary turn.
        summary = await self.append_turn(
            chat_id=chat_id,
            role=_ROLE_SYSTEM,
            content=summary_text,
            metadata={_META_KIND: _META_SUMMARY, "covers": half},
        )

        # Tombstone the compacted rows in a single UPDATE.
        oldest_ids = [t.id for t in oldest]
        await self._tombstone(oldest_ids, summary.id)

        _log.info(
            "conversation_compacted",
            chat_id=chat_id,
            ca_firm_id=str(self.ca_firm_id),
            summary_id=str(summary.id),
            turns_compacted=len(oldest_ids),
        )
        return CompactionResult(
            compacted=True,
            summary_id=summary.id,
            turns_compacted=len(oldest_ids),
            remaining_turns=turn_count - len(oldest_ids),
        )

    async def _list_live_window(
        self, *, chat_id: int, limit: int
    ) -> list[Conversation]:
        stmt = (
            self._scoped_select(Conversation)
            .where(Conversation.chat_id == chat_id)
            .where(
                ~Conversation.metadata_["compacted_into"].astext.is_not(None)
            )
            .order_by(Conversation.created_at.desc())
            .limit(limit)
        )
        rows = await self._fetch_all(stmt)
        return list(reversed(list(rows)))

    async def _summarise(self, turns: Sequence[Conversation]) -> str:
        """Call the reasoning LLM to summarise ``turns``."""
        messages = _build_summarisation_messages(turns)
        response = await call_chat(
            LLMRole.REASONING,
            messages,
            max_tokens=512,
            temperature=0.0,
        )
        text = response.content.strip()
        if not text:
            raise LLMError("summariser returned empty content")
        return text

    async def _tombstone(
        self, turn_ids: Sequence[uuid.UUID], summary_id: uuid.UUID
    ) -> None:
        """Mark the listed turns as ``compacted_into=<summary_id>``.

        Uses a JSONB concat (``||``) so existing metadata keys are
        preserved.
        """
        if not turn_ids:
            return
        # Build the metadata merge with a parameter for the summary id.
        # The expression evaluates to:
        #   metadata = metadata || jsonb_build_object('compacted_into', $value)
        stmt = (
            update(Conversation)
            .where(Conversation.id.in_(list(turn_ids)))
            .where(Conversation.ca_firm_id == self.ca_firm_id)
            .values(
                metadata_=Conversation.metadata_.op("||")(
                    {"compacted_into": str(summary_id)}
                )
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()
