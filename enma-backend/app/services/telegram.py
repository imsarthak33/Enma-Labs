"""Telegram Bot API client — the only sanctioned path for outbound Telegram I/O.

Surface (Phase 3):
    * :func:`send_message` — HTML-formatted message to a chat.
    * :func:`download_file` — download a Telegram-hosted file by ``file_id``.
    * :func:`send_document` — upload a generated file back to a chat.

Constraints enforced here:
    * Parse mode is hard-coded to ``HTML``. There is no ``parse_mode`` knob.
    * Bodies are always sent as JSON; multipart is used only for uploads.
    * Token is read lazily so test code can monkey-patch settings.
    * A single ``httpx.AsyncClient`` is reused per process; ``close_client``
      runs in the lifespan shutdown.

Rate limiting (Phase 8)
-----------------------
Telegram enforces **30 sendMessage calls per second** globally and
**1 message per second per chat**. A 429 response carries a
``Retry-After`` header (seconds to wait) or a ``parameters.retry_after``
field in the JSON body.

:func:`_post_json` wraps every API call with :func:`_with_retry` which:
  * Honours the ``Retry-After`` value from the response (capped at
    ``MAX_RETRY_AFTER_S`` so a buggy header can't stall the process).
  * Falls back to jittered exponential backoff if no header is present.
  * Retries up to ``MAX_RETRIES`` times, then raises :class:`TelegramAPIError`.
  * Only retries 429; all other non-200 statuses raise immediately.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Final

import httpx

from app.config import settings
from app.logging_setup import get_logger

_log = get_logger(__name__)

_TELEGRAM_API_BASE: Final[str] = "https://api.telegram.org"
_DEFAULT_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)

# ---------------------------------------------------------------------------
# Retry config (module-level so tests can patch without side-effects)
# ---------------------------------------------------------------------------

MAX_RETRIES: Final[int] = 3
"""Maximum number of retry attempts on a 429 response."""

BASE_BACKOFF_S: Final[float] = 1.0
"""Initial backoff interval (seconds) when no Retry-After header is present."""

MAX_RETRY_AFTER_S: Final[float] = 30.0
"""Cap on the Retry-After header value — protects against absurdly long waits."""

_client: httpx.AsyncClient | None = None


# ---------------------------------------------------------------------------
# Client lifecycle
# ---------------------------------------------------------------------------


def get_client() -> httpx.AsyncClient:
    """Return the process-wide async HTTP client, creating it lazily."""
    global _client  # noqa: PLW0603 — intentional process-singleton
    if _client is None:
        _client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
    return _client


async def close_client() -> None:
    """Close the shared client. Called from the FastAPI lifespan shutdown."""
    global _client  # noqa: PLW0603 — intentional process-singleton
    if _client is not None:
        await _client.aclose()
        _client = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _bot_token() -> str:
    return settings.telegram_bot_token.get_secret_value()


def _bot_url(method: str) -> str:
    return f"{_TELEGRAM_API_BASE}/bot{_bot_token()}/{method}"


def _file_url(file_path: str) -> str:
    return f"{_TELEGRAM_API_BASE}/file/bot{_bot_token()}/{file_path}"


class TelegramAPIError(RuntimeError):
    """Raised when the Telegram Bot API returns a non-OK response."""

    def __init__(self, method: str, status_code: int, body: str) -> None:
        super().__init__(f"Telegram API call {method!r} failed: HTTP {status_code} — {body[:200]}")
        self.method = method
        self.status_code = status_code
        self.body = body


class TelegramRateLimited(TelegramAPIError):
    """Raised when all retry attempts are exhausted after 429 responses."""

    def __init__(self, method: str, retry_after: float) -> None:
        super().__init__(method, 429, f"rate-limited, exhausted {MAX_RETRIES} retries")
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------


def _parse_retry_after(resp: httpx.Response) -> float:
    """Extract the wait time (seconds) from a 429 response.

    Telegram sends either:
    * ``Retry-After`` HTTP header (preferred).
    * ``{"parameters": {"retry_after": N}}`` in the JSON body.

    Returns a value capped at :data:`MAX_RETRY_AFTER_S`. Falls back to
    :data:`BASE_BACKOFF_S` if neither source is present or parseable.
    """
    # 1. HTTP header (most reliable).
    raw_header = resp.headers.get("Retry-After")
    if raw_header is not None:
        try:
            return min(float(raw_header), MAX_RETRY_AFTER_S)
        except ValueError:
            pass

    # 2. JSON body field.
    try:
        body = resp.json()
        params = body.get("parameters")
        if isinstance(params, dict):
            raw_json = params.get("retry_after")
            if raw_json is not None:
                return min(float(raw_json), MAX_RETRY_AFTER_S)
    except (ValueError, AttributeError):
        pass

    return BASE_BACKOFF_S


async def _with_retry(
    method: str,
    call: Any,  # async callable -> httpx.Response
    *args: Any,
    **kwargs: Any,
) -> httpx.Response:
    """Execute ``call(*args, **kwargs)`` with 429-aware retry/backoff.

    Retries up to :data:`MAX_RETRIES` times on HTTP 429. Every other
    non-200 status raises :class:`TelegramAPIError` immediately.

    Backoff strategy:
      * Attempt 1: wait = ``Retry-After`` header (or BASE_BACKOFF_S)
      * Attempt 2: wait = previous * 2 + jitter(0..1s)
      * Attempt 3: same doubling
      * After attempt 3: raise :class:`TelegramRateLimited`.
    """
    wait = BASE_BACKOFF_S
    for attempt in range(MAX_RETRIES + 1):
        resp: httpx.Response = await call(*args, **kwargs)
        if resp.status_code != 429:
            return resp
        # Extract wait time before we decide whether to retry.
        wait = _parse_retry_after(resp)
        if attempt >= MAX_RETRIES:
            _log.error(
                "telegram_rate_limit_exhausted",
                method=method,
                attempts=attempt + 1,
                final_retry_after=wait,
            )
            raise TelegramRateLimited(method, wait)
        jitter = random.uniform(0, 1)  # noqa: S311 — not crypto, just retry jitter
        sleep_s = min(wait + jitter, MAX_RETRY_AFTER_S)
        _log.warning(
            "telegram_rate_limited",
            method=method,
            attempt=attempt + 1,
            sleep_s=sleep_s,
        )
        await asyncio.sleep(sleep_s)
        # Next attempt's fallback doubles the base (exponential).
        wait = min(wait * 2, MAX_RETRY_AFTER_S)
    # Unreachable — loop always returns or raises above.
    raise TelegramRateLimited(method, wait)  # pragma: no cover


# ---------------------------------------------------------------------------
# Internal HTTP helper
# ---------------------------------------------------------------------------


async def _post_json(
    method: str,
    payload: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """POST a JSON body to ``/bot<token>/<method>`` and return ``result``.

    Raises :class:`TelegramAPIError` on non-200 or ``ok=False`` responses.
    429 responses are retried via :func:`_with_retry` before raising
    :class:`TelegramRateLimited`.
    """
    http = client or get_client()
    url = _bot_url(method)

    resp = await _with_retry(method, http.post, url, json=payload)

    if resp.status_code != httpx.codes.OK:
        _log.error(
            "telegram_http_error",
            method=method,
            status_code=resp.status_code,
            body=resp.text[:500],
        )
        raise TelegramAPIError(method, resp.status_code, resp.text)
    body: dict[str, Any] = resp.json()
    if not body.get("ok", False):
        _log.error("telegram_api_not_ok", method=method, body=body)
        raise TelegramAPIError(method, resp.status_code, resp.text)
    result = body.get("result", {})
    return result if isinstance(result, dict) else {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def send_message(
    chat_id: int,
    html_text: str,
    *,
    disable_notification: bool = False,
    reply_to_message_id: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Send an HTML-formatted message to ``chat_id``.

    ``html_text`` MUST already be Telegram-HTML; it is sent with
    ``parse_mode=HTML`` and no further processing. Use the helpers in
    :mod:`app.formatting.telegram_html` to construct it.
    """
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "disable_notification": disable_notification,
    }
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = reply_to_message_id
    return await _post_json("sendMessage", payload, client=client)


async def get_file_path(file_id: str, *, client: httpx.AsyncClient | None = None) -> str:
    """Return the relative ``file_path`` for a given Telegram ``file_id``."""
    result = await _post_json("getFile", {"file_id": file_id}, client=client)
    file_path = result.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        raise TelegramAPIError("getFile", httpx.codes.OK, str(result))
    return file_path


async def download_file(file_id: str, *, client: httpx.AsyncClient | None = None) -> bytes:
    """Resolve ``file_id`` to a download URL and return the raw bytes."""
    file_path = await get_file_path(file_id, client=client)
    http = client or get_client()
    resp = await http.get(_file_url(file_path))
    if resp.status_code != httpx.codes.OK:
        raise TelegramAPIError("file_download", resp.status_code, resp.text[:500])
    return resp.content


async def send_document(
    chat_id: int,
    file_bytes: bytes,
    filename: str,
    *,
    caption_html: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Upload ``file_bytes`` as a document to ``chat_id`` with optional HTML caption."""
    http = client or get_client()
    url = _bot_url("sendDocument")
    data: dict[str, str] = {"chat_id": str(chat_id)}
    if caption_html is not None:
        data["caption"] = caption_html
        data["parse_mode"] = "HTML"
    files = {"document": (filename, file_bytes, "application/octet-stream")}
    resp = await http.post(url, data=data, files=files)
    if resp.status_code != httpx.codes.OK:
        raise TelegramAPIError("sendDocument", resp.status_code, resp.text[:500])
    body: dict[str, Any] = resp.json()
    if not body.get("ok", False):
        raise TelegramAPIError("sendDocument", resp.status_code, resp.text)
    result = body.get("result", {})
    return result if isinstance(result, dict) else {}


__all__ = [
    "BASE_BACKOFF_S",
    "MAX_RETRIES",
    "MAX_RETRY_AFTER_S",
    "TelegramAPIError",
    "TelegramRateLimited",
    "close_client",
    "download_file",
    "get_client",
    "get_file_path",
    "send_document",
    "send_message",
]
