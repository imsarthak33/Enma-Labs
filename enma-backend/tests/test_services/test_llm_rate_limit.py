"""Tests for the LLM 429/503 retry / backoff logic."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from app.services.llm import (
    LLM_MAX_RETRIES,
    LLMError,
    LLMRole,
    _parse_llm_retry_after,
    call_chat,
)


def _resp(
    status: int,
    body: dict[str, Any] | None = None,
    retry_after: str | None = None,
) -> httpx.Response:
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    content = json.dumps(body or {}).encode()
    return httpx.Response(status, content=content, headers=headers)


def _ok_resp() -> httpx.Response:
    body = {
        "choices": [{"message": {"role": "assistant", "content": "answer"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    return _resp(200, body)


class TestParseLLMRetryAfter:
    def test_uses_header(self) -> None:
        resp = _resp(429, retry_after="10")
        assert _parse_llm_retry_after(resp, default=1.0) == 10.0

    def test_falls_back_to_default(self) -> None:
        resp = _resp(429)
        assert _parse_llm_retry_after(resp, default=3.5) == 3.5

    def test_caps_at_max(self) -> None:
        resp = _resp(429, retry_after="9999")
        from app.services.llm import _LLM_MAX_BACKOFF_S
        assert _parse_llm_retry_after(resp, default=1.0) == _LLM_MAX_BACKOFF_S


@pytest.fixture
def _configured_endpoint() -> Any:
    with (
        patch("app.services.llm.settings.reasoning_model_endpoint", new="https://llm.test/v1"),
        patch("app.services.llm.settings.reasoning_model_name", new="test-model"),
    ):
        yield


class TestCallChatRetry:
    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self, _configured_endpoint: Any) -> None:
        client = MagicMock()
        client.post = AsyncMock(return_value=_ok_resp())
        msgs = [{"role": "user", "content": "hi"}]
        result = await call_chat(LLMRole.REASONING, msgs, client=client)
        assert result.content == "answer"
        client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_retries_on_429(self, _configured_endpoint: Any) -> None:
        client = MagicMock()
        client.post = AsyncMock(side_effect=[_resp(429), _ok_resp()])
        with patch("app.services.llm.asyncio.sleep", new=AsyncMock()):
            result = await call_chat(LLMRole.REASONING, [], client=client)
        assert result.content == "answer"
        assert client.post.await_count == 2

    @pytest.mark.asyncio
    async def test_retries_on_503(self, _configured_endpoint: Any) -> None:
        client = MagicMock()
        client.post = AsyncMock(side_effect=[_resp(503), _ok_resp()])
        with patch("app.services.llm.asyncio.sleep", new=AsyncMock()):
            result = await call_chat(LLMRole.REASONING, [], client=client)
        assert result.content == "answer"

    @pytest.mark.asyncio
    async def test_exhausted_retries_raise(self, _configured_endpoint: Any) -> None:
        client = MagicMock()
        client.post = AsyncMock(return_value=_resp(429))
        with (
            patch("app.services.llm.asyncio.sleep", new=AsyncMock()),
            pytest.raises(LLMError, match="retries"),
        ):
            await call_chat(LLMRole.REASONING, [], client=client)
        assert client.post.await_count == LLM_MAX_RETRIES + 1

    @pytest.mark.asyncio
    async def test_non_retryable_status_raises_immediately(
        self, _configured_endpoint: Any
    ) -> None:
        client = MagicMock()
        client.post = AsyncMock(return_value=_resp(400))
        with pytest.raises(LLMError, match="HTTP 400"):
            await call_chat(LLMRole.REASONING, [], client=client)
        client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_respects_retry_after_header(self, _configured_endpoint: Any) -> None:
        client = MagicMock()
        client.post = AsyncMock(side_effect=[_resp(429, retry_after="5"), _ok_resp()])
        sleep_calls: list[float] = []
        async def _fake_sleep(s: float) -> None:
            sleep_calls.append(s)
        with patch("app.services.llm.asyncio.sleep", new=_fake_sleep):
            await call_chat(LLMRole.REASONING, [], client=client)
        assert sleep_calls[0] >= 5.0
