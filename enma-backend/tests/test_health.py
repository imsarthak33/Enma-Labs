"""Smoke tests for liveness/readiness endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "enma-backend"
    assert body["version"]
    assert body["env"]


def test_health_emits_request_id_header(client: TestClient) -> None:
    resp = client.get("/health")
    assert "X-Request-ID" in resp.headers
    # Echoes back when supplied.
    resp2 = client.get("/health", headers={"X-Request-ID": "test-abc-123"})
    assert resp2.headers["X-Request-ID"] == "test-abc-123"


@pytest.mark.usefixtures("fake_session_dep")
def test_ready_returns_ready_when_db_ok(client: TestClient) -> None:
    resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["database"] == "ok"


def test_unknown_route_returns_envelope(client: TestClient) -> None:
    resp = client.get("/__definitely_not_a_route__")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "http_404"


def test_validation_error_envelope(client: TestClient) -> None:
    # /health takes no params, so we hit /ready with a forced overflow via
    # malformed Accept to assert the standard envelope shape on a real 404.
    # Simpler approach: a HEAD on a GET-only path triggers 405.
    resp = client.request("DELETE", "/health")
    assert resp.status_code == 405
    assert resp.json()["error"]["code"] == "http_405"
