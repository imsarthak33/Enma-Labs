"""Locust load-test scenarios for Enma backend.

Exercises the public `/worker/*` ingress as the gateway would — building a
valid HMAC envelope per request and asserting backend acknowledgements.

Targets (Phase 8 exit criteria, doc 06):

* 50 concurrent uploading clients
* 10 simultaneous document extractions in flight
* Identity resolution p95   < 500 ms (measured separately via stage timing)
* Tax verdict end-to-end    < 2000 ms
* Gateway throughput        > 100 messages / minute

Run locally
-----------
    cd perf
    pip install -r requirements.txt
    BACKEND_URL=http://localhost:8000 \\
    GATEWAY_HMAC_SECRET=dev_hmac_secret_change_me \\
    BACKEND_API_KEY=dev_backend_api_key_change_me \\
        locust -f locustfile.py --host http://localhost:8000 \\
               --users 50 --spawn-rate 5 --run-time 5m \\
               --headless --print-stats

Headless CI mode emits a JUnit-ish summary in `perf/reports/` that is
asserted against the budget by `perf/assert_budget.py`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import random
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from locust import HttpUser, between, events, task

# -- Config ------------------------------------------------------------------

BACKEND_API_KEY = os.environ.get("BACKEND_API_KEY", "dev_backend_api_key_change_me")
GATEWAY_HMAC_SECRET = os.environ.get(
    "GATEWAY_HMAC_SECRET", "dev_hmac_secret_change_me"
).encode("utf-8")
ENVELOPE_VERSION = 1

# Simulated firm / chat IDs. The backend is firm-multi-tenant; we want the
# load to exercise multiple tenants concurrently to keep the connection pool
# honest.
SIM_FIRMS: list[str] = [str(uuid.uuid4()) for _ in range(5)]
SIM_CHATS: list[int] = [1_000_000 + i for i in range(50)]


# -- Envelope construction ---------------------------------------------------


def _build_envelope(
    kind: str,
    *,
    chat_id: int,
    message_id: int | None,
    payload: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Return (request body JSON string, outer dict). Mirrors gateway impl."""
    inner = {
        "v": ENVELOPE_VERSION,
        "kind": kind,
        "issued_at": datetime.now(UTC).isoformat(),
        "nonce": str(uuid.uuid4()),
        "chat_id": chat_id,
        "message_id": message_id,
        "update_id": random.randint(1, 1_000_000),  # noqa: S311 — perf data, not crypto
        "payload": payload,
    }
    payload_b64 = base64.b64encode(
        json.dumps(inner, separators=(",", ":")).encode()
    ).decode()
    signature = hmac.new(
        GATEWAY_HMAC_SECRET, payload_b64.encode(), hashlib.sha256
    ).hexdigest()
    outer = {
        "v": ENVELOPE_VERSION,
        "kind": kind,
        "payload_b64": payload_b64,
        "signature": signature,
    }
    return json.dumps(outer), outer


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Enma-Api-Key": BACKEND_API_KEY,
    }


# -- Scenarios ---------------------------------------------------------------


class DocumentUploadUser(HttpUser):
    """The hot path — a CA forwarding photos to the bot.

    Weighted heavily because document ingest is the dominant traffic class
    and the most sensitive to backend latency budgets.
    """

    wait_time = between(0.5, 2.0)

    @task(8)
    def upload_document(self) -> None:
        chat_id = random.choice(SIM_CHATS)  # noqa: S311
        body, outer = _build_envelope(
            "document",
            chat_id=chat_id,
            message_id=int(time.time() * 1000) & 0x7FFFFFFF,
            payload={
                "file_id": f"AgAC{uuid.uuid4().hex[:24]}",
                "caption": "March invoice",
            },
        )
        with self.client.post(
            "/worker/document",
            data=body,
            headers=_headers(),
            name="POST /worker/document",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 202):
                resp.success()
            else:
                resp.failure(f"unexpected {resp.status_code}: {resp.text[:120]}")

    @task(2)
    def text_command(self) -> None:
        chat_id = random.choice(SIM_CHATS)  # noqa: S311
        body, _ = _build_envelope(
            "command",
            chat_id=chat_id,
            message_id=int(time.time() * 1000) & 0x7FFFFFFF,
            payload={"text": "what's the ITC status for ABC Corp?"},
        )
        with self.client.post(
            "/worker/command",
            data=body,
            headers=_headers(),
            name="POST /worker/command",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 202):
                resp.success()
            else:
                resp.failure(f"unexpected {resp.status_code}")

    @task(1)
    def voice_note(self) -> None:
        chat_id = random.choice(SIM_CHATS)  # noqa: S311
        body, _ = _build_envelope(
            "voice",
            chat_id=chat_id,
            message_id=int(time.time() * 1000) & 0x7FFFFFFF,
            payload={"file_id": f"AwAC{uuid.uuid4().hex[:24]}", "duration": 7},
        )
        with self.client.post(
            "/worker/voice",
            data=body,
            headers=_headers(),
            name="POST /worker/voice",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 202):
                resp.success()
            else:
                resp.failure(f"unexpected {resp.status_code}")


class HealthCheckUser(HttpUser):
    """Background probe — ensures /health stays responsive under upload load."""

    wait_time = between(5.0, 10.0)
    weight = 1

    @task
    def ping(self) -> None:
        with self.client.get(
            "/health", name="GET /health", catch_response=True
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"health degraded: {resp.status_code}")


# -- Budget reporting --------------------------------------------------------

# Phase 8 latency budgets (milliseconds). Asserted by perf/assert_budget.py
# after a headless run.
LATENCY_BUDGETS_MS: dict[str, int] = {
    "POST /worker/document": 2000,  # full tax-verdict path
    "POST /worker/command": 800,  # supervisor ack
    "POST /worker/voice": 1000,  # voice download + transcribe ack
    "GET /health": 200,
}


@events.test_stop.add_listener
def _emit_budget_report(environment: Any, **_: Any) -> None:
    """Write a JSON summary so CI can assert the budget without parsing logs."""
    stats = environment.stats
    summary = {
        "total_requests": stats.total.num_requests,
        "total_failures": stats.total.num_failures,
        "duration_s": stats.total.last_request_timestamp - stats.total.start_time
        if stats.total.start_time
        else 0,
        "endpoints": {},
    }
    for name, entry in stats.entries.items():
        if not entry.num_requests:
            continue
        summary["endpoints"][name[1]] = {
            "num_requests": entry.num_requests,
            "num_failures": entry.num_failures,
            "p50_ms": entry.get_response_time_percentile(0.5),
            "p95_ms": entry.get_response_time_percentile(0.95),
            "p99_ms": entry.get_response_time_percentile(0.99),
            "budget_ms": LATENCY_BUDGETS_MS.get(name[1]),
        }
    out_dir = os.path.join(os.path.dirname(__file__), "reports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "perf_summary.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str)
    print(f"perf summary -> {path}")
