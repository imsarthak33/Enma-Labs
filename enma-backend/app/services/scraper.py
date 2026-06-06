"""Serper.dev client — bounded compliance-news search for the morning briefing.

ADR-007 §Decision 6: the briefing's news section is capped and cached.
We make at most ONE Serper call per IST calendar day across the entire
backend process; subsequent firms briefed on the same day reuse the
cached result.

Why a process-local cache, not Redis?
-------------------------------------
Single-VM deployment (Phase 7 baseline). When we go multi-replica in
Phase 8, this becomes a thin Redis lookup behind the same function
signature — no caller-facing change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Final

import httpx
import sentry_sdk

from app.config import settings
from app.logging_setup import get_logger
from app.utils.date_utils import now_ist

_log = get_logger(__name__)

__all__ = [
    "DEFAULT_QUERY",
    "MAX_RESULTS",
    "NewsItem",
    "ScraperError",
    "fetch_compliance_news",
    "reset_cache",
]


MAX_RESULTS: Final[int] = 3
DEFAULT_QUERY: Final[str] = "India GST compliance news"


@dataclass(frozen=True)
class NewsItem:
    """One news result rendered into the briefing."""

    title: str
    link: str
    snippet: str
    source: str | None
    published_at: str | None


class ScraperError(RuntimeError):
    """Raised on Serper outage / non-2xx. Briefing falls back gracefully."""


# Process-local cache keyed by IST date.
_cache: dict[date, tuple[NewsItem, ...]] = {}


def reset_cache() -> None:
    """Clear the cache. Exposed for tests; never called from production code."""
    _cache.clear()


async def fetch_compliance_news(
    *,
    query: str = DEFAULT_QUERY,
    limit: int = MAX_RESULTS,
    client: httpx.AsyncClient | None = None,
) -> tuple[NewsItem, ...]:
    """Return up to ``limit`` recent compliance-news items.

    Uses the in-memory IST-day cache. Returns an empty tuple (NOT an
    exception) if Serper is unconfigured or returns no results — the
    briefing renderer handles "no news today" gracefully.
    """
    if settings.serper_api_key is None:
        return ()

    today = now_ist().date()
    cached = _cache.get(today)
    if cached is not None:
        return cached[:limit]

    body = {
        "q": query,
        "num": min(limit, 10),
        "tbs": "qdr:d",  # last 24 hours
        "gl": "in",
        "hl": "en",
    }
    headers = {
        "Content-Type": "application/json",
        "X-API-KEY": settings.serper_api_key.get_secret_value(),
    }

    http = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    own_client = client is None
    try:
        try:
            resp = await http.post(settings.serper_endpoint, json=body, headers=headers)
        except httpx.HTTPError as exc:
            sentry_sdk.capture_exception(exc)
            _log.error("serper_request_failed", error=str(exc))
            raise ScraperError(f"serper request failed: {exc}") from exc

        if resp.status_code != httpx.codes.OK:
            _log.error(
                "serper_non_2xx", status=resp.status_code, body=resp.text[:300]
            )
            raise ScraperError(f"serper HTTP {resp.status_code}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise ScraperError(f"serper response not JSON: {exc}") from exc
    finally:
        if own_client:
            await http.aclose()

    items = tuple(_parse_news_items(data, limit=limit))
    _cache[today] = items
    return items


def _parse_news_items(data: dict[str, Any], *, limit: int) -> list[NewsItem]:
    """Extract ``NewsItem``s from a Serper response body.

    Serper returns results in ``news`` (when using the /news endpoint) or
    ``organic`` (the regular search endpoint); we tolerate both.
    """
    raw_results: list[dict[str, Any]] = []
    for key in ("news", "topStories", "organic"):
        candidate = data.get(key)
        if isinstance(candidate, list):
            raw_results.extend(c for c in candidate if isinstance(c, dict))
        if len(raw_results) >= limit:
            break

    items: list[NewsItem] = []
    for raw in raw_results[:limit]:
        title = raw.get("title")
        link = raw.get("link")
        if not isinstance(title, str) or not isinstance(link, str):
            continue
        items.append(
            NewsItem(
                title=title.strip(),
                link=link.strip(),
                snippet=str(raw.get("snippet") or "").strip(),
                source=raw.get("source") if isinstance(raw.get("source"), str) else None,
                published_at=raw.get("date") if isinstance(raw.get("date"), str) else None,
            )
        )
    return items
