"""Tests for the Redis cache layer.

We use ``fakeredis.aioredis`` to stay offline. The module's
process-singleton is patched before each test so the suite is hermetic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import fakeredis.aioredis
import pytest
from app.services import cache as cache_mod
from app.services.cache import (
    DEFAULT_TTL_SECONDS,
    cache_delete,
    cache_get_json,
    cache_set_json,
    cached,
    namespaced_key,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_FakeRedis = fakeredis.aioredis.FakeRedis


@pytest.fixture
async def fake_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[_FakeRedis]:
    """Replace the module's Redis client with an in-memory fake."""
    client = _FakeRedis(decode_responses=True)
    monkeypatch.setattr(cache_mod, "_client", client)
    yield client
    await client.aclose()
    monkeypatch.setattr(cache_mod, "_client", None)


@pytest.fixture
def no_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the cache module to behave as if REDIS_URL is unset."""
    monkeypatch.setattr(cache_mod, "_client", None)
    monkeypatch.setattr(cache_mod.settings, "redis_url", None)


# ---------------------------------------------------------------------------
# namespaced_key
# ---------------------------------------------------------------------------


class TestNamespacedKey:
    def test_single_part(self) -> None:
        assert namespaced_key("tax", "abc") == "enma:tax:abc"

    def test_multi_part_joined_with_colon(self) -> None:
        assert namespaced_key("rule", "firm-1", 42) == "enma:rule:firm-1:42"

    def test_no_parts(self) -> None:
        assert namespaced_key("verdict") == "enma:verdict"

    def test_empty_namespace_rejected(self) -> None:
        with pytest.raises(ValueError, match="namespace is required"):
            namespaced_key("", "x")


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


class TestCacheGetSet:
    async def test_round_trip(self, fake_redis: fakeredis.aioredis.FakeRedis) -> None:
        key = "enma:test:k1"
        await cache_set_json(key, {"v": 1, "msg": "ok"})
        assert await cache_get_json(key) == {"v": 1, "msg": "ok"}

    async def test_get_returns_none_on_miss(
        self, fake_redis: fakeredis.aioredis.FakeRedis
    ) -> None:
        assert await cache_get_json("enma:test:missing") is None

    async def test_set_respects_ttl(self, fake_redis: fakeredis.aioredis.FakeRedis) -> None:
        key = "enma:test:ttl"
        await cache_set_json(key, "v", ttl_s=42)
        # fakeredis tracks TTLs precisely
        assert await fake_redis.ttl(key) == 42

    async def test_default_ttl_is_five_minutes(
        self, fake_redis: fakeredis.aioredis.FakeRedis
    ) -> None:
        assert DEFAULT_TTL_SECONDS == 300
        await cache_set_json("enma:test:def-ttl", "x")
        assert await fake_redis.ttl("enma:test:def-ttl") == 300

    async def test_delete_removes_key(self, fake_redis: fakeredis.aioredis.FakeRedis) -> None:
        await cache_set_json("enma:test:d", 1)
        assert await cache_delete("enma:test:d") == 1
        assert await cache_get_json("enma:test:d") is None

    async def test_unserialisable_value_returns_false(
        self, fake_redis: fakeredis.aioredis.FakeRedis
    ) -> None:
        # Sets containing non-JSON-friendly types must not crash the caller.
        assert await cache_set_json("enma:test:bad", {1, 2, 3}) is False


# ---------------------------------------------------------------------------
# No-Redis (graceful degradation)
# ---------------------------------------------------------------------------


class TestNoRedisFallthrough:
    async def test_get_returns_none(self, no_redis: None) -> None:
        assert await cache_get_json("enma:any:key") is None

    async def test_set_returns_false(self, no_redis: None) -> None:
        assert await cache_set_json("enma:any:key", "v") is False

    async def test_delete_returns_zero(self, no_redis: None) -> None:
        assert await cache_delete("enma:any:key") == 0

    async def test_decorator_runs_function_uncached(self, no_redis: None) -> None:
        call_count = 0

        @cached("test")
        async def f(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x * 2

        assert await f(5) == 10
        assert await f(5) == 10
        # No cache means the function runs every time.
        assert call_count == 2


# ---------------------------------------------------------------------------
# @cached decorator
# ---------------------------------------------------------------------------


class TestCachedDecorator:
    async def test_hit_skips_function_body(
        self, fake_redis: fakeredis.aioredis.FakeRedis
    ) -> None:
        call_count = 0

        @cached("verdict")
        async def compute(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x * 10

        assert await compute(7) == 70
        assert await compute(7) == 70
        assert call_count == 1, "second call must come from cache"

    async def test_different_args_produce_different_keys(
        self, fake_redis: fakeredis.aioredis.FakeRedis
    ) -> None:
        call_count = 0

        @cached("verdict")
        async def compute(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x * 10

        assert await compute(1) == 10
        assert await compute(2) == 20
        assert call_count == 2

    async def test_kwargs_affect_key(self, fake_redis: fakeredis.aioredis.FakeRedis) -> None:
        @cached("kw")
        async def compute(*, mode: str) -> str:
            return f"out-{mode}"

        assert await compute(mode="a") == "out-a"
        assert await compute(mode="b") == "out-b"

    async def test_custom_serializer_roundtrip(
        self, fake_redis: fakeredis.aioredis.FakeRedis
    ) -> None:
        from decimal import Decimal

        @cached(
            "decimal-money",
            serializer=lambda d: str(d),
            deserializer=lambda s: Decimal(str(s)),
        )
        async def compute() -> Decimal:
            return Decimal("99999.99")

        first = await compute()
        second = await compute()
        assert first == Decimal("99999.99")
        assert second == Decimal("99999.99")
        assert type(second) is Decimal

    async def test_redis_failure_falls_through(
        self, monkeypatch: pytest.MonkeyPatch, fake_redis: fakeredis.aioredis.FakeRedis
    ) -> None:
        """If Redis.get raises, the decorator must still return the real result."""

        async def boom(*_a: object, **_kw: object) -> str:
            raise RuntimeError("simulated outage")

        monkeypatch.setattr(fake_redis, "get", boom)

        @cached("err")
        async def compute(x: int) -> int:
            return x + 1

        assert await compute(41) == 42
