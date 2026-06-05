# ADR-001: Phase 3 — Backend Worker Routes & Idempotency

**Status:** Accepted
**Date:** 2026-06-05
**Deciders:** Backend lead (implicit — single-engineer phase)
**Supersedes:** —
**Related:** `06_IMPLEMENTATION_PROGRAMME.md` §Phase 3, `03_BACKEND_ARCHITECTURE_DOCUMENT.md` §Sections B & D, `04_SECURITY_CHECKLIST_DOCUMENT.md`

---

## Context

Phase 2 ships the gateway: Telegram updates are polled, deduplicated, optionally batched (media groups), wrapped into a base64-encoded JSON inner envelope, HMAC-SHA256 signed, and POSTed fire-and-forget to one of:

```
POST /worker/document
POST /worker/document-batch
POST /worker/command
POST /worker/voice
POST /worker/callback
```

The outer envelope sent by the gateway is:

```json
{ "v": 1, "kind": "...", "payload_b64": "<base64>", "signature": "<hex sha256 hmac>" }
```

Phase 3 must accept these envelopes and:

1. **Verify** the HMAC signature on `payload_b64` *before* parsing the inner JSON.
2. **Decode** the inner envelope and extract `(chat_id, message_id, update_id, kind, payload)`.
3. **Deduplicate** on `(chat_id, message_id)` against `idempotency_log`. Repeat deliveries are a no-op that returns 200.
4. **Acknowledge** the user on Telegram ("Processing your document…") before the request returns.
5. **Schedule** the actual processing pipeline as a background task that survives the HTTP response.
6. Stay within the project's hard rules: BaseQuery for all DB I/O, HTML-only outbound Telegram text, `decimal.Decimal` for money (no money yet in Phase 3, but the formatter must already be HTML-only), a *single* `api/routes/worker.py` file as canonical entrypoint.

The Phase 3 surface excludes business logic (no extraction, no tax engine, no identity resolution). All that lands Phase 4+.

### Forces

- **Telegram at-least-once delivery.** The gateway already dedupes update_id within a 1000-entry LRU, but a poller restart or a crash between dispatch and Telegram offset-ACK can re-deliver the same update. Backend must be idempotent on `(chat_id, message_id)` to be a safety net the gateway can lean on.
- **Fire-and-forget contract.** The gateway does not await our response body. It only cares about the HTTP status for logging. We therefore have ~5s to return *something* before the gateway times out — but the actual processing pipeline (Phase 4+) will take 5–30s. Background execution is mandatory.
- **HMAC-before-parse.** The signature protects against forged envelopes; verifying it before `json.loads` removes a parse-error attack surface.
- **Supabase deployment mode (production: transaction-pooling, port 6543).** `SET LOCAL` and long-lived transactions are not safe. Each unit of work must open its own short-lived session.
- **One worker.py file.** No `worker_document.py`, `worker_command.py` splits — phase-by-phase we add handler functions inside the single module. Dispatch is by `kind` not by URL path inside the handler (URL still differs for OpenAPI clarity).

---

## Decision Summary

We adopt the following five decisions for Phase 3. Each is elaborated below.

| # | Decision | One-line rationale |
|---|----------|-------------------|
| 1 | **HMAC verification lives in a dedicated dependency** (`Depends(verify_envelope)`), not generic middleware. | Per-route activation, can return a typed `DecodedEnvelope` object, and lets the health route stay unauthenticated. |
| 2 | **Idempotency check is a second `Depends`**, ordered after envelope verification. | Two separable concerns; isolated failure modes; easy to unit-test. |
| 3 | **Background execution uses `asyncio.create_task` wrapped in a registry**, not FastAPI's `BackgroundTasks`. | `BackgroundTasks` are tied to response lifecycle and run *after* the response is sent on a single executor — but we need them to start *before* the ACK Telegram call and to survive worker shutdown cleanly. A small registry gives explicit lifecycle. |
| 4 | **Sequence: verify → idempotency → spawn background task → send Telegram ACK → return 202.** | The ACK is part of the user-visible contract and must succeed before we declare the request "accepted." If ACK fails we return 502 and let the gateway retry; idempotency makes retry safe. |
| 5 | **Signature header carries the hex HMAC** (`X-Enma-Signature`); body carries it too for redundancy. We verify the header. | Header is what Phase 2 dispatcher sets; trusting the body's `signature` field would be a self-signed loop. |

---

## Detailed Decisions

### Decision 1: Envelope verification as a FastAPI dependency

**Choice:** `verify_envelope(request: Request) -> DecodedEnvelope` registered via `Depends`.

**Why not middleware?** FastAPI/Starlette middleware runs for *every* request including `/health`. We'd then need a path allowlist inside the middleware — that's exactly the kind of "magic guard" that fails open when someone adds a new route. A per-route `Depends` makes intent explicit: every worker route's signature names the dependency.

**The dependency does:**

1. Reads `X-Enma-Api-Key` — constant-time compare with `settings.backend_api_key`. Mismatch → 401.
2. Reads `X-Enma-Signature` — required, non-empty. Missing → 401.
3. Reads the raw body. Recomputes `hmac.new(secret, body_b64, sha256).hexdigest()` over the `payload_b64` field of the parsed outer envelope. **Order matters:** parse the outer JSON to get `payload_b64`, recompute signature over that field, `hmac.compare_digest` against the header.
4. Base64-decodes `payload_b64`, `json.loads` the inner envelope, validates required fields (`v`, `kind`, `chat_id`, `nonce`, `issued_at`, `payload`).
5. Rejects if `issued_at` is more than 5 minutes old (replay protection).
6. Returns a `DecodedEnvelope` Pydantic model the route handler receives as a typed argument.

**Why hash `payload_b64` and not the full body?** That's what Phase 2's `signPayload(payloadB64, secret)` already does (see `enma-gateway/src/utils/envelope.js`). Matching that contract is non-negotiable — the gateway is already shipping.

### Decision 2: Idempotency as a separate dependency

**Choice:** `check_idempotency(envelope: DecodedEnvelope) -> IdempotencyVerdict`.

```python
class IdempotencyVerdict(BaseModel):
    is_duplicate: bool
    log_id: uuid.UUID | None   # populated only when newly inserted
```

**Logic:**
- Open a short-lived session via `Depends(get_session)`.
- `INSERT INTO idempotency_log (chat_id, message_id, update_id, payload_hash, ca_firm_id=NULL) ... ON CONFLICT (chat_id, message_id) DO NOTHING RETURNING id`.
- If `RETURNING` yields a row → `is_duplicate=False, log_id=<row>`. If it yields nothing → `is_duplicate=True`.
- This is the only place in Phase 3 that touches the DB. Since this is a single-table infrastructure write with no `ca_firm_id` filter (firm isn't resolved yet at the point we dedupe), it lives in `db/queries/idempotency.py` and is exempt from the BaseQuery pattern — but that exception is **documented in the docstring** and the file is one of two on a short allowlist (the other being `db/queries/firms.py` for firm lookup by Telegram chat_id, landing Phase 6). All other query files MUST use BaseQuery.
- Atomicity: `ON CONFLICT DO NOTHING RETURNING id` is the canonical Postgres pattern for "insert-if-new" and avoids the read-then-write race that two concurrent workers would hit.

**Why not check inside the route?** Because every worker route does the exact same check. A dependency keeps `worker.py` readable as five short handlers, not five copies of dedup boilerplate.

### Decision 3: Background task lifecycle

**Choice:** A `BackgroundTaskRegistry` singleton wrapping `asyncio.create_task`, with weak references to track in-flight tasks for graceful shutdown.

**Why not `fastapi.BackgroundTasks`?** Three reasons:
1. **Timing.** FastAPI's `BackgroundTasks` run *after* the response is sent. We want the ACK to Telegram to happen as part of the request — if the Telegram call fails, we want the gateway to see a non-2xx so it can retry. `BackgroundTasks` cannot influence the response status.
2. **Shutdown.** `BackgroundTasks` have no shutdown coordination. Uvicorn can kill them mid-execution on SIGTERM.
3. **Observability.** A registry lets us export `in_flight_tasks` as a metric (Phase 8) without monkey-patching FastAPI internals.

**Implementation sketch:**

```python
# app/utils/background.py
class BackgroundTaskRegistry:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    def spawn(self, coro: Coroutine[Any, Any, Any], *, name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if (exc := task.exception()) is not None:
            log.error("background_task_failed", task=task.get_name(), error=str(exc))
            sentry_sdk.capture_exception(exc)

    async def drain(self, timeout: float = 30.0) -> None:
        if not self._tasks: return
        await asyncio.wait(self._tasks, timeout=timeout)
```

The registry is created in the `lifespan` context manager and drained on shutdown. Phase 3 background work is just a logging stub; Phase 4+ swaps in the real pipeline.

### Decision 4: ACK sequencing

**Sequence inside a worker handler:**

```
1. verify_envelope         (Depends)  — fails fast 401
2. check_idempotency       (Depends)  — fails fast 200 if dup
3. spawn background task              — pipeline stub for now
4. send Telegram ACK message          — awaited, can fail → 502
5. return 202 Accepted                — body { "accepted": true, "log_id": ... }
```

**Why ACK before returning?** The Telegram ACK is the user-visible contract. If we return 202 but the ACK never reaches the user, the user thinks Enma is broken. Failing the request lets the gateway retry, and idempotency makes that retry a no-op for DB state — but the ACK call is re-attempted (Telegram itself is idempotent against `sendMessage` only at the Bot API layer if we pass the same `disable_notification` / parameters, so a second ACK is "harmless duplicate ack" worst case).

**Why spawn task before ACK?** If the ACK is slow but succeeds, we don't want the pipeline waiting on it. `create_task` is non-blocking; the task runs concurrently with the awaited ACK call.

**202 not 200:** "Accepted, processing asynchronously" is exactly what we mean. Idempotent duplicates return 200 (already processed) — different status, different semantics.

### Decision 5: Signature carrier

**Choice:** Trust the `X-Enma-Signature` header. Ignore the `signature` field inside the envelope body.

**Why not the body field?** The body's `signature` could be set by a forger to match a forged `payload_b64`. The only thing that's authenticated is the header *as seen by our HMAC compute*. The gateway redundantly includes `signature` inside the outer envelope for diagnostic logging only.

We verify with `hmac.compare_digest` (constant-time) — never `==`.

---

## Options Considered

### Option A: Middleware-based verification + `BackgroundTasks`

| Dimension | Assessment |
|-----------|------------|
| Complexity | Low — fewer modules. |
| Auditability | Poor — implicit path matching, easy to forget exemptions. |
| Shutdown safety | Bad — `BackgroundTasks` not drained on SIGTERM. |
| Test isolation | Hard — middleware runs in every test against the app. |

**Pros:** Less code. **Cons:** All three of FastAPI's listed weaknesses bite us.

### Option B: Per-route `Depends` + custom task registry (chosen)

| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium — three small modules (verify, dedup, registry). |
| Auditability | Strong — every protected handler names `Depends(verify_envelope)`. |
| Shutdown safety | Strong — registry drains on lifespan exit. |
| Test isolation | Easy — override deps per test. |

### Option C: Push everything onto a real queue (Redis / RabbitMQ)

| Dimension | Assessment |
|-----------|------------|
| Complexity | High — new infra, new failure modes, ops burden. |
| Scope fit | Wrong for Phase 3 — Phase 8 explicitly defers queues. |
| Latency | Worse — extra hop adds 50–200ms per envelope. |

Rejected for Phase 3. May revisit if Phase 8 perf testing finds 50 concurrent clients overwhelms in-process tasks (very unlikely given pipeline is I/O-bound on LLM calls, not CPU-bound).

---

## Trade-off Analysis

**Chosen approach trades simplicity-of-implementation for explicit lifecycle control and safety-by-default.** The cost is three extra small modules (`verify.py`, `idempotency.py`, `background.py`) and one ADR (this one) that documents *why* we didn't use FastAPI's built-in `BackgroundTasks`. The benefit is that each concern is independently testable, the audit trail for security review is obvious, and Phase 8's "background task metrics" and "graceful shutdown" requirements are already satisfied.

**Bandit risk (S-rule violations):** `hmac.compare_digest`, no `eval`, no shell — clean. The only sensitive surface is the secret comparison, which is constant-time.

---

## Consequences

**Easier:**
- Phase 4 just adds a real coroutine to the registry; no plumbing changes.
- Tests can override `verify_envelope` and `check_idempotency` independently.
- Adding `/worker/callback` and `/worker/cron/*` (Phase 7) follows the same pattern.

**Harder:**
- Backgrounded errors surface in Sentry, not in the HTTP response. We must capture exceptions inside the task wrapper or they vanish silently.
- A handler that forgets `Depends(verify_envelope)` is unauthenticated. Mitigation: CI grep check — every function in `api/routes/worker.py` must reference `verify_envelope`.

**Revisit:**
- If Phase 8 load testing shows the in-process registry doesn't scale, swap `spawn()` to enqueue on Redis Streams. Public API of the registry stays the same.
- If Telegram rate limits become a problem, the ACK send can move into the background task and the route returns 202 immediately. That's a behavioural change but a small code change.

---

## Action Items

1. [ ] **Add `app/api/middleware/envelope_verify.py`** — defines `DecodedEnvelope` Pydantic model + `verify_envelope` dependency. Strict HMAC, 5-minute replay window, structured 401 errors.
2. [ ] **Add `app/api/middleware/idempotency.py`** — defines `IdempotencyVerdict` + `check_idempotency` dependency using `INSERT ... ON CONFLICT DO NOTHING RETURNING`.
3. [ ] **Add `app/db/queries/idempotency.py`** — the *one* sanctioned non-BaseQuery DB module for Phase 3. Docstring explains the exception.
4. [ ] **Add `app/utils/background.py`** — `BackgroundTaskRegistry` with `spawn`/`drain`. Hook into `lifespan`.
5. [ ] **Add `app/services/telegram.py`** — minimum surface: `send_message(chat_id, html_text)` using httpx. HTML parse mode, no Markdown anywhere.
6. [ ] **Add `app/formatting/telegram_html.py`** — `safe_text`, `bold`, `italic`, `code`, `link`. Escape `< > &` on every interpolation.
7. [ ] **Add `app/api/routes/worker.py`** — the five routes. Phase 3 versions: verify → idempotent insert → spawn stub task → send ACK → return 202.
8. [ ] **Tests** (`tests/test_api/test_worker_phase3.py`):
   - Valid envelope → 202 + ACK sent (httpx mocked).
   - Invalid HMAC → 401, no DB row, no ACK.
   - Replay (>5 min) → 401.
   - Duplicate `(chat_id, message_id)` → 200, ACK NOT sent again.
   - Missing API key → 401.
   - Unknown `kind` → 422.
   - Background task raises → captured, response still 202.
9. [ ] **Wire into `app/api/router.py`.**
10. [ ] **CI gate:** ruff + mypy strict + pytest with ≥80% coverage. No warnings.

---

*End of ADR-001.*
