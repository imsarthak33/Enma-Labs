# Performance Test Harness

Locust scenarios that drive the backend's `/worker/*` ingress as the gateway
would — building HMAC envelopes per request and asserting against the
Phase 8 latency budgets.

## What it exercises

| Endpoint | Weight | p95 budget | Notes |
|---|---|---|---|
| `POST /worker/document` | 8 | **2000 ms** | Full extraction + tax verdict path |
| `POST /worker/command` | 2 | **800 ms** | Supervisor ack (background work continues async) |
| `POST /worker/voice` | 1 | **1000 ms** | Voice download + Whisper ack |
| `GET /health` | bg | **200 ms** | Stays responsive under upload load |

Phase 8 exit criteria require sustaining **50 concurrent uploading clients**
with **10 simultaneous document extractions** in flight and at least
**100 messages / minute** gateway throughput.

## Setup

```sh
cd perf
python -m venv .venv && . .venv/Scripts/activate    # or: source .venv/bin/activate
pip install -r requirements.txt
```

## Run locally (interactive UI)

```sh
export BACKEND_URL=http://localhost:8000
export GATEWAY_HMAC_SECRET=dev_hmac_secret_change_me
export BACKEND_API_KEY=dev_backend_api_key_change_me
locust -f locustfile.py --host "$BACKEND_URL"
# Then open http://localhost:8089
```

## Run headless (CI mode)

```sh
locust -f locustfile.py \
       --host "$BACKEND_URL" \
       --users 50 --spawn-rate 5 --run-time 5m \
       --headless --print-stats
python assert_budget.py
```

`locustfile.py` writes `perf/reports/perf_summary.json` on test stop;
`assert_budget.py` reads it and exits non-zero if any p95 or failure rate
breaches budget. Wire that into your release pipeline as a gating job.

## Tuning the budget

The latency table lives at the bottom of `locustfile.py`
(`LATENCY_BUDGETS_MS`) and is mirrored by `assert_budget.py`. Update both
in the same commit so the contract stays consistent.

## Caveats

* The harness sends synthetic envelopes; the backend's idempotency table
  fills up during the run. Truncate `idempotency_log` between runs or
  scope to a throw-away DB.
* Real Telegram throughput per chat is rate-limited (~1 msg/s per chat,
  20 msg/s per group). The 100 msg/min gateway target is the *aggregate*
  across all chats — the perf harness inherits the same property via the
  50-element `SIM_CHATS` pool.
* No real LLM calls are made — the backend short-circuits at the
  idempotency / ack boundary; downstream pipeline timings are exercised
  by the integration suite, not here.
