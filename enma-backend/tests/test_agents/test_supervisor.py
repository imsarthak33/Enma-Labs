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
    _LOOP_EXHAUSTED_FALLBACK,
    MAX_TOOL_ITERATIONS,
    TOOLS,
    SupervisorContext,
    ToolError,
    ToolSpec,
    _coerce_int_or_none,
    _coerce_uuid_or_none,
    _tool_result_carries_error,
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

    @pytest.mark.asyncio
    async def test_tool_error_forces_text_choice_on_next_call(
        self, _no_rules: Any
    ) -> None:
        """When a tool returns ``error``, the next call must use tool_choice=none.

        Regression for the production loop where ``export_to_tally`` was
        re-invoked six times on an unapproved filing because the LLM
        thought it could 'fix' the error. After the fix, iteration 2
        must be invoked with tool_choice='none' so the model has to
        produce a text reply.
        """
        ctx = SupervisorContext(
            session=AsyncMock(), ca_firm_id=uuid.uuid4(), chat_id=42
        )
        tool_call = {
            "id": "call_e",
            "type": "function",
            "function": {"name": "list_tasks", "arguments": "{}"},
        }
        first = _chat_response(tool_calls=[tool_call])
        second = _chat_response(content="Sorry, that did not work.")
        seq = [first, second]
        observed_choices: list[str] = []

        async def _next_response(*_a: Any, **kw: Any) -> ChatResponse:
            extra = kw.get("extra_body") or {}
            observed_choices.append(str(extra.get("tool_choice")))
            return seq.pop(0)

        async def _runner(_ctx: SupervisorContext, _args: dict[str, Any]) -> dict[str, Any]:
            raise ToolError("filing not approved")

        swapped = _swap_runner("list_tasks", _runner)
        with (
            patch.dict(TOOLS, {"list_tasks": swapped}, clear=False),
            patch("app.agents.supervisor.call_chat", new=_next_response),
        ):
            reply = await run_supervisor(ctx=ctx, user_text="tasks?")
        assert observed_choices == ["auto", "none"]
        assert reply.text == "Sorry, that did not work."

    @pytest.mark.asyncio
    async def test_iteration_cap_never_leaks_tool_json(self, _no_rules: Any) -> None:
        """If the budget exhausts, the user reply MUST NOT be raw tool JSON.

        Regression for the 'hi enma' incident: the model looped six
        times on export_to_tally, then the old supervisor fell through
        with messages[-1].content == '{"error": "Filing for 06/2026 ..."}'
        and showed that raw JSON to the user. After the fix, the final
        text-only call returns empty (mock still returns a tool_call
        response), and we fall back to _LOOP_EXHAUSTED_FALLBACK.
        """
        ctx = SupervisorContext(
            session=AsyncMock(), ca_firm_id=uuid.uuid4(), chat_id=42
        )
        looping = _chat_response(
            tool_calls=[
                {
                    "id": "call_loop",
                    "type": "function",
                    "function": {"name": "list_tasks", "arguments": "{}"},
                }
            ]
        )

        async def _runner(_ctx: SupervisorContext, _args: dict[str, Any]) -> dict[str, Any]:
            raise ToolError("Filing for 06/2026 has not been approved yet")

        swapped = _swap_runner("list_tasks", _runner)
        with (
            patch.dict(TOOLS, {"list_tasks": swapped}, clear=False),
            patch(
                "app.agents.supervisor.call_chat",
                new=AsyncMock(return_value=looping),
            ),
        ):
            reply = await run_supervisor(ctx=ctx, user_text="show tasks")
        assert "Filing for 06/2026" not in reply.text
        assert "error" not in reply.text.lower() or reply.text == _LOOP_EXHAUSTED_FALLBACK
        assert reply.text == _LOOP_EXHAUSTED_FALLBACK


class TestToolResultCarriesError:
    """Helper that detects ``{"error": ...}`` tool results."""

    def test_detects_error_key(self) -> None:
        assert _tool_result_carries_error('{"error": "boom"}') is True

    def test_ignores_success_result(self) -> None:
        assert _tool_result_carries_error('{"exported": true, "count": 3}') is False

    def test_ignores_non_json(self) -> None:
        assert _tool_result_carries_error("not json at all") is False

    def test_ignores_json_non_object(self) -> None:
        assert _tool_result_carries_error("[1, 2, 3]") is False
        assert _tool_result_carries_error("42") is False


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


# ---------------------------------------------------------------------------
# Refactor M2 — defensive UUID coercion + query_document_by_ref tool
# ---------------------------------------------------------------------------


class TestCoerceUuidOrNone:
    """Regression for the badly-formed-UUID supervisor crash.

    When the LLM passes the user-facing Ref hash like '4b8d91e0' (the
    first eight hex chars rendered on every Document processed summary)
    into a tool expecting a full client UUID, the previous code raised
    ``ValueError: badly formed hexadecimal UUID string`` and killed the
    background task. Now those calls surface as ``ToolError`` and the LLM
    is told to use ``query_document_by_ref`` instead.
    """

    @pytest.mark.parametrize(
        "value",
        [None, "", "null", "NULL", "None", "undefined", "  "],
    )
    def test_returns_none_for_absent_signals(self, value: object) -> None:
        assert _coerce_uuid_or_none(value) is None

    def test_returns_uuid_for_valid_string(self) -> None:
        u = uuid.uuid4()
        assert _coerce_uuid_or_none(str(u)) == u

    def test_returns_uuid_for_uuid_instance(self) -> None:
        u = uuid.uuid4()
        assert _coerce_uuid_or_none(u) is u

    def test_short_hex_prefix_raises_tool_error(self) -> None:
        """The exact crash signature from production: 8-char ref hash."""
        with pytest.raises(ToolError, match="query_document_by_ref"):
            _coerce_uuid_or_none("4b8d91e0")

    def test_other_garbage_raises_tool_error(self) -> None:
        with pytest.raises(ToolError, match="expected a UUID"):
            _coerce_uuid_or_none("not-a-uuid-at-all")

    def test_non_string_type_raises_tool_error(self) -> None:
        with pytest.raises(ToolError, match="expected a UUID"):
            _coerce_uuid_or_none(12345)


class TestQueryDocumentByRefRegistered:
    """The new ref-lookup tool must be in TOOLS so the LLM can call it."""

    def test_tool_is_registered(self) -> None:
        assert "query_document_by_ref" in TOOLS

    def test_tool_schema_requires_ref(self) -> None:
        spec = TOOLS["query_document_by_ref"]
        params = spec.parameters
        assert params["properties"]["ref"]["type"] == "string"
        assert params["required"] == ["ref"]


# ---------------------------------------------------------------------------
# W2.D — mutating tool registry surface
# ---------------------------------------------------------------------------


class TestMutatingToolsRegistered:
    """All four W2.D mutating tools must be in TOOLS with the right shape."""

    def test_add_client_registered(self) -> None:
        spec = TOOLS["add_client"]
        assert spec.parameters["required"] == ["trade_name"]
        assert "trade_name" in spec.parameters["properties"]
        assert "gstin" in spec.parameters["properties"]
        # Description has to teach the LLM the intent triggers, not just
        # name the action.
        assert "Add ABC" in spec.description or "add" in spec.description.lower()

    def test_update_client_registered(self) -> None:
        spec = TOOLS["update_client"]
        # Either client_id or client_name is needed but neither is in
        # `required` — the runner enforces "at least one" via ToolError.
        props = spec.parameters["properties"]
        assert "client_id" in props
        assert "client_name" in props
        assert "gstin" in props

    def test_mark_document_registered(self) -> None:
        spec = TOOLS["mark_document"]
        assert set(spec.parameters["required"]) == {"ref", "status"}
        assert spec.parameters["properties"]["ref"]["type"] == "string"

    def test_add_firm_rule_registered(self) -> None:
        spec = TOOLS["add_firm_rule"]
        assert spec.parameters["required"] == ["rule_text"]
        # client_id and client_name are optional — firm-wide is the default.
        assert "client_id" in spec.parameters["properties"]
        assert "client_name" in spec.parameters["properties"]


class TestMutatingToolRunners:
    """Smoke each runner with a mocked DB query layer to confirm it
    plumbs args through correctly."""

    @pytest.mark.asyncio
    async def test_add_client_happy_path(self) -> None:
        from app.agents import supervisor as sup

        fake_created = type(
            "FakeClient",
            (),
            {"id": uuid.uuid4(), "trade_name": "ABC Corp", "gstin": None},
        )()

        class _FakeClientQuery:
            def __init__(self, **_kw: Any) -> None: ...

            async def create(self, **_kw: Any) -> Any:
                return fake_created

        session = AsyncMock()
        ctx = SupervisorContext(
            session=session, ca_firm_id=uuid.uuid4(), chat_id=1
        )
        with patch.object(sup, "ClientQuery", _FakeClientQuery):
            result = await sup._tool_add_client(ctx, {"trade_name": "ABC Corp"})
        assert result["trade_name"] == "ABC Corp"
        assert result["created"] is True
        session.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_add_client_rejects_bad_gstin(self) -> None:
        from app.agents import supervisor as sup

        ctx = SupervisorContext(
            session=AsyncMock(), ca_firm_id=uuid.uuid4(), chat_id=1
        )
        with pytest.raises(ToolError, match="GSTIN"):
            await sup._tool_add_client(
                ctx, {"trade_name": "ABC Corp", "gstin": "not-a-gstin"}
            )

    @pytest.mark.asyncio
    async def test_mark_document_no_match_returns_matched_false(self) -> None:
        from app.agents import supervisor as sup

        class _FakeDocQuery:
            def __init__(self, **_kw: Any) -> None: ...

            async def find_by_ref_prefix(self, _ref: str) -> list[Any]:
                return []

        ctx = SupervisorContext(
            session=AsyncMock(), ca_firm_id=uuid.uuid4(), chat_id=1
        )
        with patch.object(sup, "DocumentQuery", _FakeDocQuery):
            result = await sup._tool_mark_document(
                ctx, {"ref": "deadbeef", "status": "approved"}
            )
        assert result == {"matched": False, "ref": "deadbeef"}

    @pytest.mark.asyncio
    async def test_add_firm_rule_firm_wide(self) -> None:
        from app.agents import supervisor as sup

        fake_rule = type(
            "FakeRule",
            (),
            {"id": uuid.uuid4(), "rule_text": "5% slab for all CLEIND invoices"},
        )()

        class _FakeRuleQuery:
            def __init__(self, **_kw: Any) -> None: ...

            async def insert_rule(self, **_kw: Any) -> Any:
                return fake_rule

        session = AsyncMock()
        ctx = SupervisorContext(
            session=session, ca_firm_id=uuid.uuid4(), chat_id=1
        )
        with patch.object(sup, "RuleQuery", _FakeRuleQuery):
            result = await sup._tool_add_firm_rule(
                ctx, {"rule_text": "5% slab for all CLEIND invoices"}
            )
        assert result["stored"] is True
        assert result["scope"] == "firm"
        assert result["client_id"] is None
