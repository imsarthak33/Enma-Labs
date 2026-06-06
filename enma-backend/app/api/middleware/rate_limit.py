"""Rate limiting — slowapi behind the ALB / gateway.

Why
---
In production the backend sits behind a public AWS ALB (and the gateway
service mirrors requests on `/worker/*`). Both surfaces could be abused
by a hostile actor — replaying envelopes, hammering supervisor commands,
or spinning up document uploads to drive LLM cost up.

The limiter sits at the ASGI middleware layer so it triggers BEFORE the
route handler runs (no business work happens for a limited client).

Storage backend
---------------
* **Production / staging:** Redis (the same ElastiCache cluster the cache
  layer uses). This makes the limit counters globally consistent across
  every ECS task — without Redis, a 2-task service would silently let
  twice the configured rate through.
* **Development / test:** in-memory. slowapi's default `MovingWindow`
  storage in a single process is fine; tests don't need shared state.

The selection happens in :func:`_storage_uri` based on
``settings.redis_url``.

Client identification
---------------------
``slowapi`` ships a key function that returns ``request.client.host``,
which is **wrong** behind a load balancer (every request would appear
to come from the ALB's private IP). We override that with
:func:`_client_ip` which:

1. Honours ``X-Forwarded-For`` (left-most entry, the original client).
2. Falls back to ``X-Real-IP`` (set by some proxies).
3. Lastly falls back to ``request.client.host``.

The ALB always appends to ``X-Forwarded-For``, so the left-most token is
the real internet-facing client.

Policy
------
The default limit (``RATE_LIMIT_DEFAULT``) applies everywhere via the
attached limiter; routes can opt into a tighter limit by using the
``@limiter.limit("rate-string")`` decorator.

The current defaults:

    * Global:                 100/minute
    * /worker/document:       30/minute  (heavy LLM path)
    * /worker/command:        60/minute
    * /worker/voice:          20/minute  (Whisper is expensive)
    * /worker/cron/*:          5/minute  (only the gateway calls these)

Cron endpoints are tagged because a misconfigured cron expression could
otherwise hammer the backend. The gateway scheduler stays well below
this ceiling.
"""

from __future__ import annotations

from typing import Final

from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.config import settings
from app.logging_setup import get_logger

_log = get_logger(__name__)

__all__ = [
    "CRON_RATE_LIMIT",
    "DOCUMENT_RATE_LIMIT",
    "VOICE_RATE_LIMIT",
    "attach_rate_limiter",
    "get_limiter",
    "limiter",
]


# ---------------------------------------------------------------------------
# Per-endpoint policy strings (slowapi rate-string format)
# ---------------------------------------------------------------------------

DOCUMENT_RATE_LIMIT: Final[str] = "30/minute"
"""Per-IP ceiling for ``/worker/document`` — heavy LLM path, low burst."""

COMMAND_RATE_LIMIT: Final[str] = "60/minute"
"""Per-IP ceiling for ``/worker/command`` — supervisor ack is cheap, but
still rate-limited to defang chat spam."""

VOICE_RATE_LIMIT: Final[str] = "20/minute"
"""Per-IP ceiling for ``/worker/voice`` — Whisper transcription is
expensive enough to warrant tighter limits."""

CRON_RATE_LIMIT: Final[str] = "5/minute"
"""Per-IP ceiling for ``/worker/cron/*`` — the gateway scheduler fires
at most once per minute per cron kind; this catches misconfiguration
without blocking legitimate traffic."""


# ---------------------------------------------------------------------------
# Client identification
# ---------------------------------------------------------------------------


def _client_ip(request: Request) -> str:
    """Return the originating client IP, ALB-aware.

    Order of trust (highest first):

    1. ``X-Forwarded-For`` — left-most token. AWS ALB always appends to
       this header; the left-most entry is the original public IP.
    2. ``X-Real-IP`` — set by some reverse proxies (not by ALB).
    3. ``request.client.host`` — only meaningful when the backend is
       directly internet-facing (local dev).
    """
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        # ``X-Forwarded-For: client, proxy1, proxy2`` — first entry is the
        # original client. Strip whitespace; defend against empty tokens.
        first = fwd.split(",", 1)[0].strip()
        if first:
            return first
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


# ---------------------------------------------------------------------------
# Storage backend
# ---------------------------------------------------------------------------


def _storage_uri() -> str:
    """Return the slowapi storage URI.

    Redis when ``REDIS_URL`` is set (distributed counters across tasks);
    in-memory otherwise (tests + local single-process dev).
    """
    if settings.redis_url is not None:
        return str(settings.redis_url)
    return "memory://"


# ---------------------------------------------------------------------------
# Limiter — module-level so route decorators can reference it
# ---------------------------------------------------------------------------

limiter: Limiter = Limiter(
    key_func=_client_ip,
    default_limits=[settings.rate_limit_default],
    storage_uri=_storage_uri(),
    headers_enabled=True,  # emit X-RateLimit-* response headers
)


def get_limiter() -> Limiter:
    """Public accessor so test code can patch the limiter cleanly."""
    return limiter


def attach_rate_limiter(app: FastAPI) -> None:
    """Wire the limiter into a FastAPI app.

    Three things happen:

    1. The ``Limiter`` instance is stashed on ``app.state.limiter`` because
       :class:`slowapi.middleware.SlowAPIMiddleware` reads it from there.
    2. The 429 exception handler is registered so a rate-limited response
       comes back as a proper JSON envelope, not a generic 500.
    3. The ASGI middleware is installed. It walks every inbound request
       and enforces the **global default limit** without touching the route
       function's signature — critical because slowapi's ``@limit`` decorator
       breaks FastAPI's ``Annotated[..., Depends(...)]`` dependency
       resolution. Per-route tighter policies (``DOCUMENT_RATE_LIMIT`` etc.)
       are kept as module-level constants for future migration to a
       dependency-based limiting approach (e.g. ``RateLimiter`` from
       ``fastapi-limiter`` or a custom ``Depends``).
    """
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    _log.info(
        "rate_limiter_attached",
        default_limit=settings.rate_limit_default,
        storage="redis" if settings.redis_url is not None else "memory",
    )
