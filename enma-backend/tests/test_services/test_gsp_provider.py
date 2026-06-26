"""Tests — GSTR-2B-via-GSP adapter (Phase 7b), env-gated factory + call shape."""

from __future__ import annotations

import httpx
from app.config import Settings
from app.services.providers.gsp import (
    HttpGspClient,
    NullGspClient,
    get_gsp_client,
)


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def test_unconfigured_returns_null_client() -> None:
    client = get_gsp_client(_settings())
    assert isinstance(client, NullGspClient)
    assert client.is_configured is False


async def test_null_client_fetches_nothing() -> None:
    out = await NullGspClient().fetch_gstr2b(
        gstin="27AABCC1234D1Z5", return_period="032026", auth_token="t"
    )
    assert out is None


def test_configured_returns_http_client() -> None:
    settings = _settings(gsp_base_url="https://gsp.example/api", gsp_api_key="key")
    assert settings.is_gsp_configured() is True
    client = get_gsp_client(settings)
    assert isinstance(client, HttpGspClient)
    assert client.is_configured is True


def test_partial_credentials_stay_dormant() -> None:
    settings = _settings(gsp_base_url="https://gsp.example/api")  # no key
    assert settings.is_gsp_configured() is False
    assert isinstance(get_gsp_client(settings), NullGspClient)


async def test_http_client_builds_authed_request(monkeypatch: object) -> None:
    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = httpx.codes.OK
        content = b'{"data": {"docdata": {}}}'

    class _FakeAsyncClient:
        def __init__(self, *_a: object, **_kw: object) -> None: ...

        async def __aenter__(self) -> _FakeAsyncClient:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        async def get(self, url: str, *, params: dict, headers: dict) -> _FakeResponse:
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return _FakeResponse()

    import app.services.providers.gsp as gsp_mod

    monkeypatch.setattr(gsp_mod.httpx, "AsyncClient", _FakeAsyncClient)  # type: ignore[attr-defined]
    client = HttpGspClient(base_url="https://gsp.example/api/", api_key="secret")
    out = await client.fetch_gstr2b(
        gstin="27AABCC1234D1Z5", return_period="032026", auth_token="otp-token"
    )
    assert out == b'{"data": {"docdata": {}}}'
    assert captured["url"] == "https://gsp.example/api/gstr2b"
    assert captured["params"] == {"gstin": "27AABCC1234D1Z5", "rtnprd": "032026"}
    assert captured["headers"]["x-api-key"] == "secret"
    assert captured["headers"]["x-auth-token"] == "otp-token"
