"""Tests for the Telegram 429 retry / backoff logic.

We patch ``asyncio.sleep`` to avoid real delays and inject fake
httpx Response objects into ``_with_retry`` directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from app.services.telegram import (
    MAX_RETRIES,
    MAX_RETRY_AFTER_S,
    TelegramRateLimited,
    _parse_retry_after,
    _with_retry,
)

# ---------------------------------------------------------------------------
# _parse_retry_after
# ---------------------------------------------------------------------------


class TestParseRetryAfter:
    def test_uses_header(self) -> None:
        resp = httpx.Response(429, headers={"Retry-After": "5"})
        assert _parse_retry_after(resp) == 5.0

    def test_caps_at_max(self) -> None:
        resp = httpx.Response(429, headers={"Retry-After": "999"})
        assert _parse_retry_after(resp) == MAX_RETRY_AFTER_S

    def test_falls_back_to_json_parameters(self) -> None:
        import json
        body = json.dumps({"parameters": {"retry_after": 8}})
        resp = httpx.Response(429, content=body.encode())
        assert _parse_retry_after(resp) == 8.0

    def test_no_header_returns_base(self) -> None:
        resp = httpx.Response(429)
        from app.services.telegram import BASE_BACKOFF_S
        assert _parse_retry_after(resp) == BASE_BACKOFF_S

    def test_invalid_header_falls_back(self) -> None:
        resp = httpx.Response(429, headers={"Retry-After": "not-a-number"})
        from app.services.telegram import BASE_BACKOFF_S
        assert _parse_retry_after(resp) == BASE_BACKOFF_S


# ---------------------------------------------------------------------------
# _with_retry
# ---------------------------------------------------------------------------


def _resp(status: int, retry_after: str | None = None) -> httpx.Response:
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return httpx.Response(status, headers=headers)


class TestWithRetry:
    @pytest.mark.asyncio
    async def test_non_429_returns_immediately(self) -> None:
        call = AsyncMock(return_value=_resp(200))
        result = await _with_retry("sendMessage", call)
        assert result.status_code == 200
        call.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_single_429_then_success(self) -> None:
        responses = [_resp(429, retry_after="1"), _resp(200)]
        call = AsyncMock(side_effect=responses)
        with patch("app.services.telegram.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            result = await _with_retry("sendMessage", call)
        assert result.status_code == 200
        assert call.await_count == 2
        sleep_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_respects_retry_after_header(self) -> None:
        responses = [_resp(429, retry_after="7"), _resp(200)]
        call = AsyncMock(side_effect=responses)
        sleep_calls: list[float] = []
        async def _fake_sleep(s: float) -> None:
            sleep_calls.append(s)
        with patch("app.services.telegram.asyncio.sleep", new=_fake_sleep):
            await _with_retry("sendMessage", call)
        # Sleep must be >= 7s (the Retry-After value), accounting for jitter.
        assert sleep_calls[0] >= 7.0

    @pytest.mark.asyncio
    async def test_max_retries_exhausted_raises(self) -> None:
        call = AsyncMock(return_value=_resp(429, retry_after="0"))
        with (
            patch("app.services.telegram.asyncio.sleep", new=AsyncMock()),
            pytest.raises(TelegramRateLimited),
        ):
            await _with_retry("sendMessage", call)
        # Called MAX_RETRIES + 1 times (initial + retries).
        assert call.await_count == MAX_RETRIES + 1

    @pytest.mark.asyncio
    async def test_non_429_non_200_returns_resp(self) -> None:
        """500 is not retried — returned as-is for the caller to raise on."""
        call = AsyncMock(return_value=_resp(500))
        result = await _with_retry("sendMessage", call)
        assert result.status_code == 500
        call.assert_awaited_once()
