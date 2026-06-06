"""Tests for the Serper scraper — cache, parsing, error fallback."""

from __future__ import annotations

import datetime as _dt
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from app.services import scraper
from pydantic import SecretStr


class _Resp:
    status_code = 200

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body
        self.text = "ok"

    def json(self) -> dict[str, Any]:
        return self._body


@pytest.fixture(autouse=True)
def _isolate_cache_and_key() -> Any:
    scraper.reset_cache()
    with patch.object(
        scraper.settings, "serper_api_key", new=SecretStr("test-key")
    ):
        yield
    scraper.reset_cache()


class TestFetchComplianceNews:
    @pytest.mark.asyncio
    async def test_parses_news_payload(self) -> None:
        body = {
            "news": [
                {
                    "title": "CBIC issues clarification",
                    "link": "https://example.com/1",
                    "snippet": "GST circular …",
                    "source": "CBIC",
                    "date": "2h ago",
                },
                {
                    "title": "ITAT ruling",
                    "link": "https://example.com/2",
                    "snippet": "",
                },
            ]
        }
        client = AsyncMock()
        client.post = AsyncMock(return_value=_Resp(body))
        items = await scraper.fetch_compliance_news(client=client)
        assert len(items) == 2
        assert items[0].title == "CBIC issues clarification"
        assert items[0].source == "CBIC"

    @pytest.mark.asyncio
    async def test_cache_returns_same_results(self) -> None:
        body = {"news": [{"title": "X", "link": "https://example.com/x"}]}
        client = AsyncMock()
        client.post = AsyncMock(return_value=_Resp(body))
        a = await scraper.fetch_compliance_news(client=client)
        b = await scraper.fetch_compliance_news(client=client)
        # One HTTP call total despite two fetch_compliance_news invocations.
        assert client.post.await_count == 1
        assert a == b

    @pytest.mark.asyncio
    async def test_no_key_returns_empty(self) -> None:
        with patch.object(scraper.settings, "serper_api_key", new=None):
            scraper.reset_cache()
            items = await scraper.fetch_compliance_news()
        assert items == ()

    @pytest.mark.asyncio
    async def test_non_200_raises(self) -> None:
        bad = _Resp({})
        bad.status_code = 500
        client = AsyncMock()
        client.post = AsyncMock(return_value=bad)
        with pytest.raises(scraper.ScraperError):
            await scraper.fetch_compliance_news(client=client)

    @pytest.mark.asyncio
    async def test_cache_keyed_by_ist_date(self) -> None:
        """Two calls on the same IST day share the cache; a date roll-over busts it."""
        body = {"news": [{"title": "X", "link": "https://example.com/x"}]}
        client = AsyncMock()
        client.post = AsyncMock(return_value=_Resp(body))

        real_now = scraper.now_ist
        day1 = _dt.datetime(2026, 6, 6, 9, tzinfo=_dt.UTC)
        day2 = _dt.datetime(2026, 6, 7, 9, tzinfo=_dt.UTC)
        try:
            with patch.object(scraper, "now_ist", new=lambda: day1):
                await scraper.fetch_compliance_news(client=client)
            with patch.object(scraper, "now_ist", new=lambda: day2):
                await scraper.fetch_compliance_news(client=client)
        finally:
            scraper.now_ist = real_now  # type: ignore[assignment]
        # Two distinct IST dates → two calls.
        assert client.post.await_count == 2
