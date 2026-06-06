"""Redis cache layer — single source of cached state for the backend.

Surface
-------
* :func:`get_redis` — lazy process-singleton :class:`redis.asyncio.Redis`.
* :func:`close_redis` — lifespan-shutdown hook to drain the pool.
* :func:`cached` — decorator that memoises an ``async`` function's
  JSON-serialisable return value with a TTL.
* :func:`cache_get_json` / :func:`cache_set_json` / :func:`cache_delete` —
  low-level helpers for ad-hoc caching outside the decorator.
* :func:`namespaced_key` — canonical key construction (``enma:<ns>:<...>``).

Production invariants
---------------------
* The cache is an **optimisation**, never a correctness boundary. Every
  call is wrapped in :func:`_safe` so a Redis outage or network blip
  silently falls through to the underlying function — we'd rather pay
  the recompute cost than 5xx a CA's invoice upload.
* TLS is enforced in production: ElastiCache Serverless requires
  ``rediss://`` URLs (note the double-s). Local dev uses plain ``redis://``.
* Keys are namespaced with the ``enma:`` prefix so a shared cache (e.g.
  a hand-rolled Redis instance) doesn't collide.
* Values are JSON-serialised. Callers that need to cache
  ``decimal.Decimal`` (every money path does) must pass a custom
  ``serializer`` / ``deserializer`` pair to :func:`cached` — *never* call
  ``float()`` on a money value to get JSON-friendly output.

The module degrades gracefully if ``REDIS_URL`` is unset: every cache
operation becomes a no-op and ``cached()`` simply runs the underlying
function. This keeps the test suite offline and Phase 0-4 deployments
runnable without provisioning a cache.
"""

from __future__ import annotations

import functools
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, Final, ParamSpec, TypeVar, cast

import redis.asyncio as redis_async
import sentry_sdk

from app.config import settings
from app.logging_setup import get_logger

_log = get_logger(__name__)

__all__ = [
    "DEFAULT_TTL_SECONDS",
    "KEY_PREFIX",
    "cache_delete",
    "cache_get_json",
    "cache_set_json",
    "cached",
    "close_redis",
    "get_redis",
    "namespaced_key",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KEY_PREFIX: Final[str] = "enma:"
"""Every cached value uses this prefix so a shared cache stays partitioned."""

DEFAULT_TTL_SECONDS: Final[int] = 300
"""Five-minute default TTL — short enough that stale data clears itself,
long enough to absorb the burst of identical extraction calls within a
single upload session."""

_DEFAULT_TIMEOUT_S: Final[float] = 1.0
"""Network timeout per Redis command. Cache miss is preferable to a
multi-second hang that would breach :data:`worker.document` latency budget."""


# ---------------------------------------------------------------------------
# Client lifecycle
# ---------------------------------------------------------------------------

_client: redis_async.Redis | None = None


def get_redis() -> redis_async.Redis | None:
    """Return the process-wide async Redis client, or ``None`` if disabled.

    ``None`` is the explicit signal "no cache configured" — every helper in
    this module accepts that case and falls through to the underlying
    operation. There is no automatic local-Redis fallback.
    """
    global _client  # noqa: PLW0603 — intentional process-singleton
    if _client is not None:
        return _client
    url = settings.redis_url
    if url is None:
        return None
    _client = redis_async.from_url(
        url,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=_DEFAULT_TIMEOUT_S,
        socket_timeout=_DEFAULT_TIMEOUT_S,
        health_check_interval=30,
    )
    return _client


async def close_redis() -> None:
    """Close the shared client. Called from the FastAPI lifespan shutdown."""
    global _client  # noqa: PLW0603 — intentional process-singleton
    if _client is not None:
        try:
            await _client.aclose()
        finally:
            _client = None


# ---------------------------------------------------------------------------
# Key construction
# ---------------------------------------------------------------------------


def namespaced_key(namespace: str, *parts: str | int) -> str:
    """Build a canonical cache key: ``enma:<namespace>:<part>:<part>...``.

    Use this rather than f-string formatting so the prefix and separator
    stay consistent across every cache site.
    """
    if not namespace:
        raise ValueError("namespace is required")
    joined = ":".join(str(p) for p in parts)
    return f"{KEY_PREFIX}{namespace}:{joined}" if joined else f"{KEY_PREFIX}{namespace}"


def _hash_signature(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """Stable SHA-256 of the positional + keyword arguments.

    Used by :func:`cached` to derive a key from the call site's inputs.
    Falls back to ``repr`` for anything that isn't JSON-serialisable so we
    never raise from inside the decorator — at worst we get a less
    deduplication-friendly key.
    """

    def _default(obj: Any) -> Any:
        return repr(obj)

    payload = json.dumps(
        {"args": args, "kwargs": kwargs},
        sort_keys=True,
        default=_default,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Safe helpers — every call swallows Redis errors and falls through
# ---------------------------------------------------------------------------


async def _safe_get(client: redis_async.Redis, key: str) -> str | None:
    try:
        value = await client.get(key)
    except Exception as exc:  # — cache must not propagate errors
        _log.warning("cache_get_failed", key=key, error=str(exc))
        sentry_sdk.capture_exception(exc)
        return None
    return cast("str | None", value)


async def _safe_set(client: redis_async.Redis, key: str, value: str, ttl_s: int) -> bool:
    try:
        await client.set(key, value, ex=ttl_s)
    except Exception as exc:  # — cache must not propagate errors
        _log.warning("cache_set_failed", key=key, error=str(exc))
        sentry_sdk.capture_exception(exc)
        return False
    return True


async def _safe_delete(client: redis_async.Redis, key: str) -> int:
    try:
        return int(await client.delete(key))
    except Exception as exc:  # — cache must not propagate errors
        _log.warning("cache_delete_failed", key=key, error=str(exc))
        sentry_sdk.capture_exception(exc)
        return 0


# ---------------------------------------------------------------------------
# Public low-level helpers
# ---------------------------------------------------------------------------


async def cache_get_json(key: str) -> Any | None:
    """Return the JSON-deserialised value for ``key``, or ``None`` on miss."""
    client = get_redis()
    if client is None:
        return None
    raw = await _safe_get(client, key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        _log.warning("cache_decode_failed", key=key, error=str(exc))
        return None


async def cache_set_json(key: str, value: Any, ttl_s: int = DEFAULT_TTL_SECONDS) -> bool:
    """JSON-serialise and store ``value`` under ``key``. Returns ``True`` on success."""
    client = get_redis()
    if client is None:
        return False
    try:
        payload = json.dumps(value, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        _log.warning("cache_encode_failed", key=key, error=str(exc))
        return False
    return await _safe_set(client, key, payload, ttl_s)


async def cache_delete(key: str) -> int:
    """Delete a key. Returns the number of keys actually removed (0 or 1)."""
    client = get_redis()
    if client is None:
        return 0
    return await _safe_delete(client, key)


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

P = ParamSpec("P")
R = TypeVar("R")


def cached(
    namespace: str,
    *,
    ttl_s: int = DEFAULT_TTL_SECONDS,
    serializer: Callable[[R], Any] | None = None,
    deserializer: Callable[[Any], R] | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Decorator that caches the JSON-serialisable return of an async function.

    Usage::

        @cached("tax_verdict", ttl_s=600)
        async def compute_tax_verdict(...) -> dict[str, str]: ...

    The cache key is ``enma:<namespace>:<sha256-of-args>``. Functions whose
    inputs contain unhashable types should still work — :func:`_hash_signature`
    falls back to ``repr``.

    Pass ``serializer`` / ``deserializer`` for types that JSON can't round-
    trip natively (e.g. ``decimal.Decimal``): the serializer converts the
    return value into a JSON-friendly shape, the deserializer reverses it.

    On any Redis error the decorator silently bypasses the cache — the
    underlying function always runs at least once per logical call.
    """

    def _decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(func)
        async def _wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            client = get_redis()
            if client is None:
                return await func(*args, **kwargs)

            sig = _hash_signature(args, kwargs)
            key = namespaced_key(namespace, sig)

            raw = await _safe_get(client, key)
            if raw is not None:
                try:
                    decoded = json.loads(raw)
                except json.JSONDecodeError:
                    decoded = None
                if decoded is not None:
                    return deserializer(decoded) if deserializer is not None else cast("R", decoded)

            result = await func(*args, **kwargs)
            payload: Any = serializer(result) if serializer is not None else result
            try:
                encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
            except (TypeError, ValueError) as exc:
                _log.warning(
                    "cache_encode_failed_in_decorator",
                    namespace=namespace,
                    error=str(exc),
                )
                return result
            await _safe_set(client, key, encoded, ttl_s)
            return result

        return _wrapped

    return _decorator
