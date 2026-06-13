"""Tests for the LLM client.

We exercise the wire shape with ``httpx.MockTransport`` — never a real API.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from app.services import llm
from app.services.llm import (
    LLMError,
    LLMRole,
    NoEndpointConfigured,
    build_image_content,
    build_pdf_content,
    call_chat,
)


def _chat_response(
    content: str = '{"ok": true}', usage: dict[str, int] | None = None
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": usage or {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
    }


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# build_image_content
# ---------------------------------------------------------------------------


class TestBuildImageContent:
    def test_detects_png_magic(self) -> None:
        part = build_image_content(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
        assert part["type"] == "image_url"
        assert part["image_url"]["url"].startswith("data:image/png;base64,")

    def test_detects_jpeg_magic(self) -> None:
        part = build_image_content(b"\xff\xd8\xff" + b"\x00" * 8)
        assert part["image_url"]["url"].startswith("data:image/jpeg;base64,")

    def test_detects_webp(self) -> None:
        part = build_image_content(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8)
        assert part["image_url"]["url"].startswith("data:image/webp;base64,")

    def test_explicit_mime_overrides_detection(self) -> None:
        part = build_image_content(b"unrecognised", mime="image/png")
        assert part["image_url"]["url"].startswith("data:image/png;base64,")

    def test_unknown_without_explicit_mime_raises(self) -> None:
        with pytest.raises(LLMError, match="MIME"):
            build_image_content(b"\x00" * 16)


class TestBuildPdfContent:
    def test_shape(self) -> None:
        part = build_pdf_content(b"%PDF-fake")
        assert part["type"] == "file"
        assert part["file"]["file_data"].startswith("data:application/pdf;base64,")
        assert part["file"]["filename"] == "document.pdf"


# ---------------------------------------------------------------------------
# call_chat
# ---------------------------------------------------------------------------


class TestCallChat:
    @pytest.mark.asyncio
    async def test_happy_path_returns_content_and_usage(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(httpx.codes.OK, json=_chat_response())

        async with _client(handler) as ac:
            resp = await call_chat(
                LLMRole.LAYOUT,
                messages=[{"role": "user", "content": "ping"}],
                response_format={"type": "json_object"},
                max_tokens=200,
                client=ac,
            )

        assert resp.content == '{"ok": true}'
        assert resp.input_tokens == 12
        assert resp.output_tokens == 7

        # Verify body shape.
        sent = json.loads(seen[0].content.decode())
        assert sent["messages"] == [{"role": "user", "content": "ping"}]
        assert sent["temperature"] == 0.0
        assert sent["max_tokens"] == 200
        assert sent["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_max_tokens_is_capped_by_ceiling(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(llm.settings, "llm_max_output_tokens", 100)
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content.decode()))
            return httpx.Response(httpx.codes.OK, json=_chat_response())

        async with _client(handler) as ac:
            await call_chat(
                LLMRole.EXTRACTION,
                messages=[{"role": "user", "content": "x"}],
                max_tokens=10_000,
                client=ac,
            )

        assert captured[0]["max_tokens"] == 100

    @pytest.mark.asyncio
    async def test_non_2xx_raises_llm_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="kaboom")

        async with _client(handler) as ac:
            with pytest.raises(LLMError, match="HTTP 500"):
                await call_chat(
                    LLMRole.REASONING,
                    messages=[{"role": "user", "content": "x"}],
                    client=ac,
                )

    @pytest.mark.asyncio
    async def test_missing_choices_raises_llm_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(httpx.codes.OK, json={"weird": "shape"})

        async with _client(handler) as ac:
            with pytest.raises(LLMError, match="choices"):
                await call_chat(
                    LLMRole.LAYOUT,
                    messages=[{"role": "user", "content": "x"}],
                    client=ac,
                )

    @pytest.mark.asyncio
    async def test_non_string_content_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            payload = _chat_response()
            payload["choices"][0]["message"]["content"] = {"not": "string"}
            return httpx.Response(httpx.codes.OK, json=payload)

        async with _client(handler) as ac:
            with pytest.raises(LLMError, match="not a string"):
                await call_chat(
                    LLMRole.LAYOUT,
                    messages=[{"role": "user", "content": "x"}],
                    client=ac,
                )

    @pytest.mark.asyncio
    async def test_empty_endpoint_raises_no_endpoint_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(llm.settings, "layout_model_endpoint", "")
        with pytest.raises(NoEndpointConfigured):
            await call_chat(
                LLMRole.LAYOUT,
                messages=[{"role": "user", "content": "x"}],
                client=_client(lambda _r: httpx.Response(httpx.codes.OK)),
            )

    @pytest.mark.asyncio
    async def test_http_error_wrapped_as_llm_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("conn refused")

        async with _client(handler) as ac:
            with pytest.raises(LLMError, match="LLM request failed"):
                await call_chat(
                    LLMRole.LAYOUT,
                    messages=[{"role": "user", "content": "x"}],
                    client=ac,
                )

    @pytest.mark.asyncio
    async def test_authorization_header_set_when_api_key_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pydantic import SecretStr

        monkeypatch.setattr(llm.settings, "llm_api_key", SecretStr("sk-test"))
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(httpx.codes.OK, json=_chat_response())

        async with _client(handler) as ac:
            await call_chat(
                LLMRole.LAYOUT,
                messages=[{"role": "user", "content": "x"}],
                client=ac,
            )

        assert captured[0].headers["Authorization"] == "Bearer sk-test"


class TestClientLifecycle:
    @pytest.mark.asyncio
    async def test_get_client_singleton(self) -> None:
        a = llm.get_client()
        b = llm.get_client()
        assert a is b
        await llm.close_client()


class TestParallelToolCallsCompat:
    """Regression guard for refactor task A — NIM Llama parallel-tool-call fix.

    The bug: ``meta/llama-3.3-70b-instruct`` on NVIDIA NIM rejects responses
    that emit multiple ``tool_calls`` in one assistant turn with HTTP 400
    ``"This model only supports single tool-calls at once!"``. That error
    was breaking every supervisor call, including casual chat that didn't
    need tools at all. See ``app/services/llm.py:call_chat``.
    """

    @pytest.mark.asyncio
    async def test_parallel_tool_calls_defaults_to_false_when_tools_present(self) -> None:
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content.decode()))
            return httpx.Response(httpx.codes.OK, json=_chat_response())

        async with _client(handler) as ac:
            await call_chat(
                LLMRole.REASONING,
                messages=[{"role": "user", "content": "hi"}],
                extra_body={"tools": [{"type": "function", "function": {"name": "x"}}]},
                client=ac,
            )

        assert captured[0]["parallel_tool_calls"] is False

    @pytest.mark.asyncio
    async def test_no_parallel_tool_calls_when_no_tools(self) -> None:
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content.decode()))
            return httpx.Response(httpx.codes.OK, json=_chat_response())

        async with _client(handler) as ac:
            await call_chat(
                LLMRole.REASONING,
                messages=[{"role": "user", "content": "hi"}],
                client=ac,
            )

        assert "parallel_tool_calls" not in captured[0]

    @pytest.mark.asyncio
    async def test_caller_override_wins(self) -> None:
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content.decode()))
            return httpx.Response(httpx.codes.OK, json=_chat_response())

        async with _client(handler) as ac:
            await call_chat(
                LLMRole.REASONING,
                messages=[{"role": "user", "content": "hi"}],
                extra_body={
                    "tools": [{"type": "function", "function": {"name": "x"}}],
                    "parallel_tool_calls": True,  # explicit caller intent
                },
                client=ac,
            )

        assert captured[0]["parallel_tool_calls"] is True

    @pytest.mark.asyncio
    async def test_400_single_tool_calls_retries_with_tool_choice_none(self) -> None:
        """The canonical NIM 400 triggers a one-shot retry with tool_choice=none.

        This is the graceful path for casual chat: the model answers as plain
        text instead of demanding a database tool. No LLMError surfaces.
        """
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode())
            captured.append(body)
            if body.get("tool_choice") == "none":
                return httpx.Response(
                    httpx.codes.OK,
                    json=_chat_response(content="Hi! How can I help today?"),
                )
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": (
                            "This model only supports single tool-calls at once! "
                            "This model only supports single tool-calls at once!"
                        ),
                        "type": "BadRequestError",
                        "code": 400,
                    }
                },
            )

        async with _client(handler) as ac:
            resp = await call_chat(
                LLMRole.REASONING,
                messages=[{"role": "user", "content": "hi"}],
                extra_body={
                    "tools": [{"type": "function", "function": {"name": "x"}}],
                    "tool_choice": "auto",
                },
                client=ac,
            )

        assert resp.content == "Hi! How can I help today?"
        assert len(captured) == 2
        assert captured[0]["tool_choice"] == "auto"
        assert captured[0]["parallel_tool_calls"] is False
        assert captured[1]["tool_choice"] == "none"
        # parallel_tool_calls must be dropped for the retry to avoid double-fail.
        assert "parallel_tool_calls" not in captured[1]

    @pytest.mark.asyncio
    async def test_unrelated_400_still_raises(self) -> None:
        """Generic 400s (bad request body, invalid model) must still error out."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"error": {"message": "invalid model name"}},
            )

        async with _client(handler) as ac:
            with pytest.raises(LLMError, match="HTTP 400"):
                await call_chat(
                    LLMRole.REASONING,
                    messages=[{"role": "user", "content": "hi"}],
                    extra_body={"tools": [{"type": "function", "function": {"name": "x"}}]},
                    client=ac,
                )
