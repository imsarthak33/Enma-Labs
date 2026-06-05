"""Unit tests for the envelope_verify dependency.

We bypass HTTP and call the dependency directly with a fake Starlette
``Request`` so we can isolate the verification logic from idempotency and
route handlers.
"""

from __future__ import annotations

import base64
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.api.middleware.envelope_verify import (
    ENVELOPE_VERSION,
    REPLAY_WINDOW_SECONDS,
    verify_envelope,
)
from app.config import settings
from fastapi import HTTPException
from starlette.requests import Request

API_KEY = settings.backend_api_key.get_secret_value()
SECRET = settings.gateway_hmac_secret.get_secret_value()


def _inner(
    *,
    kind: str = "document",
    chat_id: int | None = 999,
    message_id: int | None = 42,
    update_id: int | None = 7,
    now: datetime | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issued = (now or datetime.now(UTC)).isoformat()
    return {
        "v": ENVELOPE_VERSION,
        "kind": kind,
        "issued_at": issued,
        "nonce": str(uuid.uuid4()),
        "chat_id": chat_id,
        "message_id": message_id,
        "update_id": update_id,
        "payload": payload or {"hello": "world"},
    }


def _outer(inner: dict[str, Any]) -> tuple[bytes, str]:
    payload_b64 = base64.b64encode(json.dumps(inner).encode("utf-8")).decode("ascii")
    sig = hmac.new(SECRET.encode(), payload_b64.encode("ascii"), "sha256").hexdigest()
    body = json.dumps(
        {"v": ENVELOPE_VERSION, "kind": inner["kind"], "payload_b64": payload_b64, "signature": sig}
    ).encode("utf-8")
    return body, sig


def _fake_request(body: bytes) -> Request:
    """Build a minimal Starlette Request that yields ``body`` for ``await request.body()``."""

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/worker/document",
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 0),
    }
    return Request(scope, receive)


@pytest.mark.asyncio
async def test_valid_envelope_returns_decoded() -> None:
    inner = _inner()
    body, sig = _outer(inner)
    env = await verify_envelope(
        _fake_request(body),
        x_enma_api_key=API_KEY,
        x_enma_signature=sig,
        x_enma_envelope_version=str(ENVELOPE_VERSION),
    )
    assert env.kind == "document"
    assert env.chat_id == 999
    assert env.message_id == 42
    assert env.payload == {"hello": "world"}


@pytest.mark.asyncio
async def test_missing_api_key_401() -> None:
    inner = _inner()
    body, sig = _outer(inner)
    with pytest.raises(HTTPException) as exc:
        await verify_envelope(_fake_request(body), x_enma_api_key=None, x_enma_signature=sig)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_wrong_api_key_401() -> None:
    inner = _inner()
    body, sig = _outer(inner)
    with pytest.raises(HTTPException) as exc:
        await verify_envelope(
            _fake_request(body),
            x_enma_api_key="not-the-real-key",
            x_enma_signature=sig,
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_missing_signature_401() -> None:
    inner = _inner()
    body, _ = _outer(inner)
    with pytest.raises(HTTPException) as exc:
        await verify_envelope(_fake_request(body), x_enma_api_key=API_KEY, x_enma_signature=None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_bad_signature_401() -> None:
    inner = _inner()
    body, _ = _outer(inner)
    with pytest.raises(HTTPException) as exc:
        await verify_envelope(
            _fake_request(body),
            x_enma_api_key=API_KEY,
            x_enma_signature="0" * 64,
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_replay_too_old_401() -> None:
    inner = _inner(now=datetime.now(UTC) - timedelta(seconds=REPLAY_WINDOW_SECONDS + 60))
    body, sig = _outer(inner)
    with pytest.raises(HTTPException) as exc:
        await verify_envelope(_fake_request(body), x_enma_api_key=API_KEY, x_enma_signature=sig)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_future_skew_too_far_401() -> None:
    inner = _inner(now=datetime.now(UTC) + timedelta(seconds=REPLAY_WINDOW_SECONDS + 60))
    body, sig = _outer(inner)
    with pytest.raises(HTTPException) as exc:
        await verify_envelope(_fake_request(body), x_enma_api_key=API_KEY, x_enma_signature=sig)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_outer_inner_kind_mismatch_400() -> None:
    inner = _inner(kind="document")
    payload_b64 = base64.b64encode(json.dumps(inner).encode("utf-8")).decode("ascii")
    sig = hmac.new(SECRET.encode(), payload_b64.encode("ascii"), "sha256").hexdigest()
    body = json.dumps(
        {"v": ENVELOPE_VERSION, "kind": "voice", "payload_b64": payload_b64, "signature": sig}
    ).encode("utf-8")
    with pytest.raises(HTTPException) as exc:
        await verify_envelope(_fake_request(body), x_enma_api_key=API_KEY, x_enma_signature=sig)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_unknown_kind_400() -> None:
    inner = _inner(kind="document")
    inner["kind"] = "garbage"  # bypass our helper validation
    payload_b64 = base64.b64encode(json.dumps(inner).encode("utf-8")).decode("ascii")
    sig = hmac.new(SECRET.encode(), payload_b64.encode("ascii"), "sha256").hexdigest()
    body = json.dumps(
        {"v": ENVELOPE_VERSION, "kind": "garbage", "payload_b64": payload_b64, "signature": sig}
    ).encode("utf-8")
    with pytest.raises(HTTPException) as exc:
        await verify_envelope(_fake_request(body), x_enma_api_key=API_KEY, x_enma_signature=sig)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_invalid_base64_400() -> None:
    body = json.dumps(
        {
            "v": ENVELOPE_VERSION,
            "kind": "document",
            "payload_b64": "!!!not-base64!!!",
            "signature": "x",
        }
    ).encode("utf-8")
    # We have to sign that bogus payload_b64 so the HMAC check passes
    # — otherwise we'd never reach the base64 decode.
    sig = hmac.new(SECRET.encode(), b"!!!not-base64!!!", "sha256").hexdigest()
    with pytest.raises(HTTPException) as exc:
        await verify_envelope(_fake_request(body), x_enma_api_key=API_KEY, x_enma_signature=sig)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_empty_body_400() -> None:
    with pytest.raises(HTTPException) as exc:
        await verify_envelope(_fake_request(b""), x_enma_api_key=API_KEY, x_enma_signature="x")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_envelope_version_header_mismatch_400() -> None:
    inner = _inner()
    body, sig = _outer(inner)
    with pytest.raises(HTTPException) as exc:
        await verify_envelope(
            _fake_request(body),
            x_enma_api_key=API_KEY,
            x_enma_signature=sig,
            x_enma_envelope_version="99",
        )
    assert exc.value.status_code == 400
