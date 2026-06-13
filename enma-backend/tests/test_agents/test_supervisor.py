"""Tests for the supervisor agent.

We mock :func:`app.services.llm.call_chat` so no network hits happen.
The fakes return a sequence of OpenAI-style chat-completions responses,
letting us assert:

* a single-turn (no tool call) reply works,
* multi-turn tool calls thread through correctly,
* the iteration cap (``MAX_TOOL_ITERATIONS``) is honoured.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from app.agents.supervisor import (
    MAX_TOOL_ITERATIONS,
    TOOLS,
    SupervisorContext,
    ToolError,
    ToolSpec,
    _coerce_int_or_none,
    run_supervisor,
)
from app.services.llm import ChatResponse


def _swap_runner(name: str, runner: Any) -> ToolSpec:
    """Build a replacement ToolSpec keeping schema, swapping the runner."""
    spec = TOOLS[name]
    return ToolSpec(
        name=spec.name,
        description=spec.description,
        parameters=spec.parameters,
        runner=runner,
    )


def _chat_response(
    *,
    content: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
    input_tokens: int = 1,
    output_tokens: int = 1,
) -> ChatResponse:
    """Build a ChatResponse mirroring the raw shape the supervisor reads."""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    raw = {"choices": [{"message": message}], "usage": {}}
    return ChatResponse(
        content=content,
        model="test-reasoning",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        raw=raw,
    )


@pytest.fixture
def _no_rules() -> Any:
    """Patch the rules loader to a no-op so we don't need a real DB."""
    with patch(
        "app.agents.supervisor.load_rules_for_engine",
        new=AsyncMock(return_value=()),
    ) as p:
        yield p


class TestRunSupervisor:
    @pytest.mark.asyncio
    async def test_plain_reply_no_tools(self, _no_rules: Any) -> None:
        ctx = SupervisorContext(
            session=AsyncMock(),
            ca_firm_id=uuid.uuid4(),
            chat_id=42,
        )
        with patch(
            "app.agents.supervisor.call_chat",
            new=AsyncMock(return_value=_chat_response(content="ITC for ABC is ₹12.")),
        ):
            reply = await run_supervisor(ctx=ctx, user_text="ITC for ABC?")
        assert reply.text == "ITC for ABC is ₹12."
        assert reply.tool_calls_made == 0
        assert reply.input_tokens == 1
        assert reply.output_tokens == 1

    @pytest.mark.asyncio
    async def test_iteration_cap_honoured(self, _no_rules: Any) -> None:
        """A model that endlessly asks for a tool stops at the budget."""
        ctx = SupervisorContext(
            session=AsyncMock(), ca_firm_id=uuid.uuid4(), chat_id=42
        )
        looping = _chat_response(
            tool_calls=[
                {
                    "id": "call_loop",
                    "type": "function",
                    "function": {
                        "name": "list_tasks",
                        "arguments": "{}",
                    },
                }
            ]
        )

        async def _runner(_ctx: SupervisorContext, _args: dict[str, Any]) -> dict[str, Any]:
            return {"count": 0, "tasks": []}

        swapped = _swap_runner("list_tasks", _runner)
        with (
            patch.dict(TOOLS, {"list_tasks": swapped}, clear=False),
            patch(
                "app.agents.supervisor.call_chat",
                new=AsyncMock(return_value=looping),
            ),
        ):
            reply = await run_supervisor(ctx=ctx, user_text="show tasks")
        assert reply.tool_calls_made == MAX_TOOL_ITERATIONS

    @pytest.mark.asyncio
    async def test_tool_call_then_text(self, _no_rules: Any) -> None:
        """One tool call, then a final text reply."""
        ctx = SupervisorContext(
            session=AsyncMock(), ca_firm_id=uuid.uuid4(), chat_id=42
        )
        tool_call = {
            "id": "call_a",
            "type": "function",
            "function": {"name": "list_tasks", "arguments": "{}"},
        }
        first = _chat_response(tool_calls=[tool_call])
        second = _chat_response(content="No open tasks.")
        seq = [first, second]

        async def _next_response(*_a: Any, **_kw: Any) -> ChatResponse:
            return seq.pop(0)

        async def _runner(_ctx: SupervisorContext, _args: dict[str, Any]) -> dict[str, Any]:
            return {"count": 0, "tasks": []}

        swapped = _swap_runner("list_tasks", _runner)
        with (
            patch.dict(TOOLS, {"list_tasks": swapped}, clear=False),
            patch("app.agents.supervisor.call_chat", new=_next_response),
        ):
            reply = await run_supervisor(ctx=ctx, user_text="tasks?")
        assert reply.text == "No open tasks."
        assert reply.tool_calls_made == 1

    @pytest.mark.asyncio
    async def test_tool_error_surfaced_to_model(self, _no_rules: Any) -> None:
        """ToolError becomes a JSON ``{error}`` tool result the model can read."""
        ctx = SupervisorContext(
            session=AsyncMock(), ca_firm_id=uuid.uuid4(), chat_id=42
        )
        tool_call = {
            "id": "call_b",
            "type": "function",
            "function": {"name": "list_tasks", "arguments": "{}"},
        }
        first = _chat_response(tool_calls=[tool_call])
        second = _chat_response(content="OK ignoring the failure.")
        seq = [first, second]

        async def _next_response(*_a: Any, **_kw: Any) -> ChatResponse:
            return seq.pop(0)

        async def _runner(_ctx: SupervisorContext, _args: dict[str, Any]) -> dict[str, Any]:
            raise ToolError("nope")

        swapped = _swap_runner("list_tasks", _runner)
        with (
            patch.dict(TOOLS, {"list_tasks": swapped}, clear=False),
            patch("app.agents.supervisor.call_chat", new=_next_response),
        ):
            reply = await run_supervisor(ctx=ctx, user_text="tasks?")
        assert "OK ignoring the failure" in reply.text


class TestToolSchemas:
    def test_every_tool_has_schema_and_runner(self) -> None:
        for name, spec in TOOLS.items():
            assert spec.name == name
            assert callable(spec.runner)
            assert "type" in spec.parameters
            assert spec.parameters["type"] == "object"

    def test_to_openai_tool_shape(self) -> None:
        for spec in TOOLS.values():
            payload = spec.to_openai_tool()
            assert payload["type"] == "function"
            fn = payload["function"]
            assert fn["name"] == spec.name
            assert isinstance(fn["description"], str)
            assert json.dumps(fn["parameters"])  # JSON-serialisable


# ---------------------------------------------------------------------------
# Refactor M — defensive int coercion for LLM-emitted tool arguments
# ---------------------------------------------------------------------------


class TestCoerceIntOrNone:
    """Regression for the supervisor int('null') crash.

    The supervisor LLM serialises an "absent" filing_period_year as the
    literal string "null" instead of omitting the field. Previously
    ``int("null")`` raised ValueError, crashed the background task, and
    the user saw an indefinite "Working on it..." with no reply.
    """

    @pytest.mark.parametrize(
        "value",
        [None, "", "null", "NULL", "None", "none", "undefined", "  null  "],
    )
    def test_returns_none_for_absent_signals(self, value: object) -> None:
        assert _coerce_int_or_none(value) is None

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (5, 5),
            ("5", 5),
            ("  10 ", 10),
            (2025, 2025),
            ("2025", 2025),
            (3.0, 3),
        ],
    )
    def test_coerces_valid_ints(self, value: object, expected: int) -> None:
        assert _coerce_int_or_none(value) == expected

    @pytest.mark.parametrize("bad", ["abc", "2025.5", "ten", "1e3"])
    def test_invalid_strings_raise_tool_error(self, bad: str) -> None:
        with pytest.raises(ToolError, match="expected an integer"):
            _coerce_int_or_none(bad)

    def test_bool_raises_tool_error(self) -> None:
        # JSON parses true/false as Python bool — refuse silent coercion to 0/1.
        with pytest.raises(ToolError, match="boolean"):
            _coerce_int_or_none(True)

    def test_non_integer_float_raises(self) -> None:
        with pytest.raises(ToolError, match="expected an integer"):
            _coerce_int_or_none(3.14)
