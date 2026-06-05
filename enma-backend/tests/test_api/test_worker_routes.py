"""End-to-end tests for the Phase 3 worker routes.

We exercise the full middleware stack via TestClient but override:

  * ``check_idempotency`` → returns a stub verdict (no DB hit).
  * ``app.services.telegram.send_message`` → monkey-patched async stub.
  * ``app.api.routes.worker._phase3_stub_pipeline`` → monkey-patched async stub.

This isolates the request/response contract from infrastructure while still
running envelope verification end-to-end.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from app.api.middleware.envelope_verify import ENVELOPE_VERSION
from app.api.middleware.idempotency import IdempotencyVerdict, check_idempotency
from app.api.routes import worker as worker_module
from app.config import settings
from app.services import telegram as telegram_service
from fastapi.testclient import TestClient

API_KEY = settings.backend_api_key.get_secret_value()
SECRET = settings.gateway_hmac_secret.get_secret_value()


def _inner(**overrides: Any) -> dict[str, Any]:
    base = {
        "v": ENVELOPE_VERSION,
        "kind": "document",
        "issued_at": datetime.now(UTC).isoformat(),
        "nonce": str(uuid.uuid4()),
        "chat_id": 999,
        "message_id": 42,
        "update_id": 7,
        "payload": {"file_id": "abc"},
    }
    base.update(overrides)
    return base


def _envelope_body(inner: dict[str, Any], *, kind_override: str | None = None) -> tuple[str, str]:
    payload_b64 = base64.b64encode(json.dumps(inner).encode("utf-8")).decode("ascii")
    sig = hmac.new(SECRET.encode(), payload_b64.encode("ascii"), "sha256").hexdigest()
    outer = {
        "v": ENVELOPE_VERSION,
        "kind": kind_override or inner["kind"],
        "payload_b64": payload_b64,
        "signature": sig,
    }
    return json.dumps(outer), sig


def _headers(sig: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Enma-Api-Key": API_KEY,
        "X-Enma-Signature": sig,
        "X-Enma-Envelope-Version": str(ENVELOPE_VERSION),
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ack_recorder(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture every call to ``services.telegram.send_message``."""
    calls: list[dict[str, Any]] = []

    async def fake_send_message(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"message_id": 1}

    monkeypatch.setattr(telegram_service, "send_message", fake_send_message)
    # The worker imports send_message via ``from app.services import telegram``,
    # so patching the attribute on the module is enough.
    return calls


@pytest.fixture()
def pipeline_recorder(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Capture invocations of every background pipeline coroutine.

    Phase 4 split the worker into two background coroutines:
    ``_run_document_pipeline`` (the real extraction flow) and
    ``_phase3_stub_pipeline`` (the placeholder for command/voice/callback
    kinds). For these Phase-3-style integration tests we patch BOTH to
    a no-op recorder so we exercise the routing contract without
    touching the DB or the LLM.
    """
    invocations: list[Any] = []
    done = asyncio.Event()

    async def fake_pipeline(envelope: Any) -> None:
        invocations.append(envelope)
        done.set()

    monkeypatch.setattr(worker_module, "_phase3_stub_pipeline", fake_pipeline)
    monkeypatch.setattr(worker_module, "_run_document_pipeline", fake_pipeline)
    invocations.append(done)  # so tests can await done via invocations[0]
    return invocations


@pytest.fixture()
def overridden_idempotency(app_instance) -> Iterator[dict[str, Any]]:
    """Override the idempotency dependency. Default verdict: new (not duplicate)."""
    state: dict[str, Any] = {
        "verdict": IdempotencyVerdict(is_duplicate=False, log_id=uuid.uuid4()),
        "calls": 0,
    }

    async def fake_check() -> IdempotencyVerdict:
        state["calls"] += 1
        return state["verdict"]

    app_instance.dependency_overrides[check_idempotency] = fake_check
    try:
        yield state
    finally:
        app_instance.dependency_overrides.pop(check_idempotency, None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_document_valid_envelope_returns_202_and_acks(
    client: TestClient,
    ack_recorder: list[dict[str, Any]],
    pipeline_recorder: list[Any],
    overridden_idempotency: dict[str, Any],
) -> None:
    inner = _inner(kind="document")
    body, sig = _envelope_body(inner)
    res = client.post("/worker/document", content=body, headers=_headers(sig))
    assert res.status_code == 202, res.text
    payload = res.json()
    assert payload["accepted"] is True
    assert payload["duplicate"] is False
    assert payload["log_id"]
    # ACK sent with HTML parse mode (italic wrapper).
    assert len(ack_recorder) == 1
    assert ack_recorder[0]["chat_id"] == 999
    assert "<i>" in ack_recorder[0]["html_text"]
    # Pipeline scheduled — give the loop a tick to run the task.
    done: asyncio.Event = pipeline_recorder[0]
    asyncio.run(_wait(done))
    assert any(getattr(e, "kind", None) == "document" for e in pipeline_recorder[1:])


async def _wait(evt: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(evt.wait(), timeout=2.0)
    except TimeoutError:  # pragma: no cover — test-failure path
        raise AssertionError("background pipeline did not run") from None


def test_duplicate_returns_200_no_ack_no_pipeline(
    client: TestClient,
    ack_recorder: list[dict[str, Any]],
    pipeline_recorder: list[Any],
    overridden_idempotency: dict[str, Any],
) -> None:
    overridden_idempotency["verdict"] = IdempotencyVerdict(is_duplicate=True, log_id=None)
    inner = _inner(kind="document")
    body, sig = _envelope_body(inner)
    res = client.post("/worker/document", content=body, headers=_headers(sig))
    assert res.status_code == 200
    payload = res.json()
    assert payload["accepted"] is True
    assert payload["duplicate"] is True
    assert payload["log_id"] is None
    assert ack_recorder == []
    # Only the [0] sentinel event should be in the recorder list.
    assert len(pipeline_recorder) == 1


def test_invalid_hmac_returns_401_no_side_effects(
    client: TestClient,
    ack_recorder: list[dict[str, Any]],
    pipeline_recorder: list[Any],
    overridden_idempotency: dict[str, Any],
) -> None:
    inner = _inner(kind="document")
    body, _ = _envelope_body(inner)
    res = client.post(
        "/worker/document",
        content=body,
        headers=_headers("0" * 64),
    )
    assert res.status_code == 401
    assert ack_recorder == []
    assert len(pipeline_recorder) == 1
    assert overridden_idempotency["calls"] == 0


def test_missing_api_key_returns_401(
    client: TestClient, overridden_idempotency: dict[str, Any]
) -> None:
    inner = _inner(kind="document")
    body, sig = _envelope_body(inner)
    headers = _headers(sig)
    del headers["X-Enma-Api-Key"]
    res = client.post("/worker/document", content=body, headers=headers)
    assert res.status_code == 401


def test_kind_url_mismatch_returns_400(
    client: TestClient,
    ack_recorder: list[dict[str, Any]],
    pipeline_recorder: list[Any],
    overridden_idempotency: dict[str, Any],
) -> None:
    # Inner says "document" but we POST to /worker/voice.
    inner = _inner(kind="document")
    body, sig = _envelope_body(inner, kind_override="voice")
    res = client.post("/worker/voice", content=body, headers=_headers(sig))
    # Outer/inner kind mismatch is caught by verify_envelope → 400.
    assert res.status_code == 400


def test_each_route_accepts_its_kind(
    client: TestClient,
    ack_recorder: list[dict[str, Any]],
    pipeline_recorder: list[Any],
    overridden_idempotency: dict[str, Any],
) -> None:
    cases = {
        "document": "/worker/document",
        "document_batch": "/worker/document-batch",
        "command": "/worker/command",
        "voice": "/worker/voice",
        "callback": "/worker/callback",
    }
    for kind, route in cases.items():
        overridden_idempotency["verdict"] = IdempotencyVerdict(
            is_duplicate=False, log_id=uuid.uuid4()
        )
        inner = _inner(kind=kind)
        body, sig = _envelope_body(inner)
        res = client.post(route, content=body, headers=_headers(sig))
        assert res.status_code == 202, f"{route} → {res.status_code}: {res.text}"


def test_ack_failure_returns_502(
    client: TestClient,
    pipeline_recorder: list[Any],
    overridden_idempotency: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(**_kwargs: Any) -> dict[str, Any]:
        raise telegram_service.TelegramAPIError("sendMessage", 500, "down")

    monkeypatch.setattr(telegram_service, "send_message", boom)
    inner = _inner(kind="document")
    body, sig = _envelope_body(inner)
    res = client.post("/worker/document", content=body, headers=_headers(sig))
    # TelegramAPIError → translated to a 502 Bad Gateway so the gateway retries.
    assert res.status_code == 502
