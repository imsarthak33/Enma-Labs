"""Tests for the conversation-log compaction logic.

We keep the test scope tight: the token estimator and compaction
trigger are pure functions / branches we can exercise without a real
Postgres. Full DB round-trips are covered by integration tests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from app.db.queries.conversations import (
    COMPACT_TRIGGER_TOKENS,
    COMPACT_TRIGGER_TURNS,
    CompactionResult,
    ConversationQuery,
    estimate_tokens,
)
from app.services.llm import ChatResponse


class TestEstimateTokens:
    @pytest.mark.parametrize(
        "text, lo, hi",
        [
            ("", 0, 0),
            ("a", 1, 1),
            ("abcd", 1, 2),
            ("abcdefgh", 2, 3),
        ],
    )
    def test_rough_bounds(self, text: str, lo: int, hi: int) -> None:
        n = estimate_tokens(text)
        assert lo <= n <= hi


def _fake_turn(content: str, idx: int) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        role="user" if idx % 2 == 0 else "assistant",
        content=content,
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        metadata_={},
    )


class TestMaybeCompact:
    @pytest.mark.asyncio
    async def test_no_op_when_under_thresholds(self) -> None:
        ca_firm = uuid.uuid4()
        q = ConversationQuery(session=AsyncMock(), ca_firm_id=ca_firm)
        few_turns = [_fake_turn("hi", i) for i in range(3)]
        with patch.object(
            ConversationQuery,
            "_list_live_window",
            new=AsyncMock(return_value=few_turns),
        ):
            result = await q.maybe_compact(chat_id=1)
        assert result == CompactionResult(
            compacted=False,
            summary_id=None,
            turns_compacted=0,
            remaining_turns=3,
        )

    @pytest.mark.asyncio
    async def test_compacts_when_over_turn_trigger(self) -> None:
        ca_firm = uuid.uuid4()
        q = ConversationQuery(session=AsyncMock(), ca_firm_id=ca_firm)
        many = [_fake_turn("turn", i) for i in range(COMPACT_TRIGGER_TURNS + 4)]

        chat_response = ChatResponse(
            content="Summary text.",
            model="test",
            input_tokens=5,
            output_tokens=5,
            raw={"choices": [{"message": {"content": "Summary text."}}], "usage": {}},
        )
        summary_turn = SimpleNamespace(id=uuid.uuid4())

        with (
            patch.object(
                ConversationQuery,
                "_list_live_window",
                new=AsyncMock(return_value=many),
            ),
            patch.object(
                ConversationQuery,
                "append_turn",
                new=AsyncMock(return_value=summary_turn),
            ),
            patch.object(
                ConversationQuery, "_tombstone", new=AsyncMock(return_value=None)
            ),
            patch(
                "app.db.queries.conversations.call_chat",
                new=AsyncMock(return_value=chat_response),
            ),
        ):
            result = await q.maybe_compact(chat_id=1)
        assert result.compacted is True
        assert result.summary_id == summary_turn.id
        assert result.turns_compacted > 0

    @pytest.mark.asyncio
    async def test_token_trigger_fires(self) -> None:
        ca_firm = uuid.uuid4()
        q = ConversationQuery(session=AsyncMock(), ca_firm_id=ca_firm)
        # 5 turns, each long enough that the total comfortably beats the
        # token trigger.
        big_text = "x" * (COMPACT_TRIGGER_TOKENS * 4)
        big_turns = [_fake_turn(big_text, i) for i in range(5)]
        summary_turn = SimpleNamespace(id=uuid.uuid4())
        chat_response = ChatResponse(
            content="OK", model="t", input_tokens=1, output_tokens=1, raw={}
        )
        with (
            patch.object(
                ConversationQuery,
                "_list_live_window",
                new=AsyncMock(return_value=big_turns),
            ),
            patch.object(
                ConversationQuery,
                "append_turn",
                new=AsyncMock(return_value=summary_turn),
            ),
            patch.object(
                ConversationQuery, "_tombstone", new=AsyncMock(return_value=None)
            ),
            patch(
                "app.db.queries.conversations.call_chat",
                new=AsyncMock(return_value=chat_response),
            ),
        ):
            result = await q.maybe_compact(chat_id=1)
        assert result.compacted is True


class TestAppendTurnValidation:
    @pytest.mark.asyncio
    async def test_invalid_role_raises(self) -> None:
        ca_firm = uuid.uuid4()
        q = ConversationQuery(session=AsyncMock(), ca_firm_id=ca_firm)
        with pytest.raises(ValueError, match="unknown conversation role"):
            await q.append_turn(chat_id=1, role="bot", content="hi")
