"""Tests for the rate-limiting middleware.

We exercise the ``_client_ip`` extractor independently of slowapi (pure
function, deterministic), then build a minimal FastAPI app with the
limiter attached to confirm that a 429 fires after the configured burst.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from app.api.middleware import rate_limit as rl
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

# ---------------------------------------------------------------------------
# _client_ip — ALB-aware key function
# ---------------------------------------------------------------------------


def _make_request(headers: dict[str, str], host: str | None = "5.5.5.5") -> Request:
    """Build a minimal Starlette ``Request`` with the given headers."""
    scope: dict[str, object] = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (host, 12345) if host else None,
    }
    return Request(scope)  # type: ignore[arg-type]


class TestClientIp:
    def test_x_forwarded_for_left_most_wins(self) -> None:
        req = _make_request({"x-forwarded-for": "1.2.3.4, 10.0.0.1, 10.0.0.2"})
        assert rl._client_ip(req) == "1.2.3.4"

    def test_x_forwarded_for_single_value(self) -> None:
        req = _make_request({"x-forwarded-for": "9.9.9.9"})
        assert rl._client_ip(req) == "9.9.9.9"

    def test_x_real_ip_fallback(self) -> None:
        req = _make_request({"x-real-ip": "2.2.2.2"})
        assert rl._client_ip(req) == "2.2.2.2"

    def test_request_client_host_last_resort(self) -> None:
        req = _make_request({}, host="3.3.3.3")
        assert rl._client_ip(req) == "3.3.3.3"

    def test_unknown_when_no_signal(self) -> None:
        req = _make_request({}, host=None)
        assert rl._client_ip(req) == "unknown"

    def test_xff_strips_whitespace(self) -> None:
        req = _make_request({"x-forwarded-for": "   8.8.8.8 , 1.1.1.1"})
        assert rl._client_ip(req) == "8.8.8.8"

    def test_empty_xff_falls_through(self) -> None:
        req = _make_request({"x-forwarded-for": ""}, host="6.6.6.6")
        assert rl._client_ip(req) == "6.6.6.6"


# ---------------------------------------------------------------------------
# Storage URI selection
# ---------------------------------------------------------------------------


class TestStorageUri:
    def test_memory_when_no_redis(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rl.settings, "redis_url", None)
        assert rl._storage_uri() == "memory://"

    def test_redis_url_used_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rl.settings, "redis_url", "redis://localhost:6379/0")
        assert rl._storage_uri() == "redis://localhost:6379/0"


# ---------------------------------------------------------------------------
# Integration — 429 after the burst
# ---------------------------------------------------------------------------


@pytest.fixture
def app_with_limiter() -> Iterator[TestClient]:
    """Build a minimal app whose ``/ping`` route allows 2 requests per minute."""
    limiter = Limiter(key_func=rl._client_ip, default_limits=[], storage_uri="memory://")
    app = FastAPI()
    app.state.limiter = limiter
    from slowapi import _rate_limit_exceeded_handler

    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    @app.get("/ping")
    @limiter.limit("2/minute")
    async def ping(request: Request) -> dict[str, str]:
        return {"ok": "pong"}

    yield TestClient(app)


class TestRateLimitFires:
    def test_third_request_returns_429(self, app_with_limiter: TestClient) -> None:
        # Use a fixed XFF so every request lands in the same bucket.
        headers = {"X-Forwarded-For": "1.1.1.1"}
        for _ in range(2):
            assert app_with_limiter.get("/ping", headers=headers).status_code == 200
        third = app_with_limiter.get("/ping", headers=headers)
        assert third.status_code == 429

    def test_separate_clients_have_independent_buckets(
        self, app_with_limiter: TestClient
    ) -> None:
        a = {"X-Forwarded-For": "1.1.1.1"}
        b = {"X-Forwarded-For": "2.2.2.2"}
        for _ in range(2):
            assert app_with_limiter.get("/ping", headers=a).status_code == 200
            assert app_with_limiter.get("/ping", headers=b).status_code == 200
        # A is exhausted, B still has budget
        assert app_with_limiter.get("/ping", headers=a).status_code == 429
        # But because both share the global default (none here), B is also
        # exhausted on its own bucket — both got 2/2.
        assert app_with_limiter.get("/ping", headers=b).status_code == 429
