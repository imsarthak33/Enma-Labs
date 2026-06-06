"""Unit tests for the cron-envelope verifier.

Same helper shape as ``test_envelope_verify.py`` — we POST a synthesised
outer envelope and assert acceptance / rejection.
"""

from __future__ import annotations

import base64
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from app.api.middleware.envelope_verify import (
    CRON_KINDS,
    ENVELOPE_VERSION,
    verify_cron_envelope,
)
from app.config import settings
from fastapi import HTTPException
from starlette.requests import Request

API_KEY = settings.backend_api_key.get_secret_value()
SECRET = settings.gateway_hmac_secret.get_secret_value()


def _inner(
    *,
    kind: str = "cron_morning_briefing",
    chat_id: int | None = None,
    scheduled_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    issued = (now or datetime.now(UTC)).isoformat()
    payload: dict[str, Any] = {}
    if scheduled_at is not None:
        payload["scheduled_at"] = scheduled_at
    return {
        "v": ENVELOPE_VERSION,
        "kind": kind,
        "issued_at": issued,
        "nonce": str(uuid.uuid4()),
        "chat_id": chat_id,
        "message_id": None,
        "update_id": None,
        "payload": payload,
    }


def _outer(inner: dict[str, Any]) -> tuple[bytes, str]:
    payload_b64 = base64.b64encode(json.dumps(inner).encode("utf-8")).decode("ascii")
    sig = hmac.new(SECRET.encode(), payload_b64.encode("ascii"), "sha256").hexdigest()
    body = json.dumps(
        {
            "v": ENVELOPE_VERSION,
            "kind": inner["kind"],
            "payload_b64": payload_b64,
            "signature": sig,
        }
    ).encode("utf-8")
    return body, sig


def _fake_request(body: bytes) -> Request:
    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/worker/cron/morning-briefing",
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 0),
    }
    return Request(scope, receive)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_cron_envelope_returns_decoded() -> None:
    inner = _inner(scheduled_at="2026-06-06T03:30:00+00:00")
    body, sig = _outer(inner)
    env = await verify_cron_envelope(
        _fake_request(body),
        x_enma_api_key=API_KEY,
        x_enma_signature=sig,
        x_enma_envelope_version=str(ENVELOPE_VERSION),
    )
    assert env.kind == "cron_morning_briefing"
    assert env.chat_id is None
    assert env.payload["scheduled_at"] == "2026-06-06T03:30:00+00:00"


@pytest.mark.asyncio
async def test_all_cron_kinds_accepted() -> None:
    for kind in CRON_KINDS:
        inner = _inner(kind=kind, scheduled_at="2026-06-06T03:30:00+00:00")
        body, sig = _outer(inner)
        env = await verify_cron_envelope(
            _fake_request(body),
            x_enma_api_key=API_KEY,
            x_enma_signature=sig,
            x_enma_envelope_version=str(ENVELOPE_VERSION),
        )
        assert env.kind == kind


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_envelope_kind_rejected() -> None:
    inner = _inner(kind="document", scheduled_at="2026-06-06T03:30:00+00:00")
    body, sig = _outer(inner)
    with pytest.raises(HTTPException) as exc:
        await verify_cron_envelope(
            _fake_request(body),
            x_enma_api_key=API_KEY,
            x_enma_signature=sig,
            x_enma_envelope_version=str(ENVELOPE_VERSION),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_chat_id_not_none_rejected() -> None:
    inner = _inner(chat_id=42, scheduled_at="2026-06-06T03:30:00+00:00")
    body, sig = _outer(inner)
    with pytest.raises(HTTPException) as exc:
        await verify_cron_envelope(
            _fake_request(body),
            x_enma_api_key=API_KEY,
            x_enma_signature=sig,
            x_enma_envelope_version=str(ENVELOPE_VERSION),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_missing_scheduled_at_rejected() -> None:
    inner = _inner(scheduled_at=None)
    body, sig = _outer(inner)
    with pytest.raises(HTTPException) as exc:
        await verify_cron_envelope(
            _fake_request(body),
            x_enma_api_key=API_KEY,
            x_enma_signature=sig,
            x_enma_envelope_version=str(ENVELOPE_VERSION),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_missing_api_key_rejected() -> None:
    inner = _inner(scheduled_at="2026-06-06T03:30:00+00:00")
    body, sig = _outer(inner)
    with pytest.raises(HTTPException) as exc:
        await verify_cron_envelope(
            _fake_request(body),
            x_enma_api_key=None,
            x_enma_signature=sig,
            x_enma_envelope_version=str(ENVELOPE_VERSION),
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_bad_hmac_rejected() -> None:
    inner = _inner(scheduled_at="2026-06-06T03:30:00+00:00")
    body, _sig = _outer(inner)
    with pytest.raises(HTTPException) as exc:
        await verify_cron_envelope(
            _fake_request(body),
            x_enma_api_key=API_KEY,
            x_enma_signature="0" * 64,
            x_enma_envelope_version=str(ENVELOPE_VERSION),
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_missing_signature_header_rejected() -> None:
    inner = _inner(scheduled_at="2026-06-06T03:30:00+00:00")
    body, _sig = _outer(inner)
    with pytest.raises(HTTPException) as exc:
        await verify_cron_envelope(
            _fake_request(body),
            x_enma_api_key=API_KEY,
            x_enma_signature=None,
            x_enma_envelope_version=str(ENVELOPE_VERSION),
        )
    assert exc.value.status_code == 401
