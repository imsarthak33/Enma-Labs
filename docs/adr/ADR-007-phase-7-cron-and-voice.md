# ADR-007: Phase 7 — Cron Operations, Executive Functions & Voice Notes

**Status:** Accepted
**Date:** 2026-06-06
**Deciders:** Backend lead (implicit — single-engineer phase)
**Supersedes:** —
**Related:** ADR-001 (Phase 3 worker), `06_IMPLEMENTATION_PROGRAMME.md` §Phase 7, `03_BACKEND_ARCHITECTURE_DOCUMENT.md` §Sections B & D

---

## Context

Phases 0–6 deliver every event-driven path: Telegram updates flow through the gateway, an envelope hits the backend worker, identity resolution + the extraction pipeline produce a verdict, the supervisor handles text queries, and filing approvals lock periods immutably. What we lack is everything that has to happen **without a user trigger**:

1. **Task heartbeat** (`*/5 * * * *`). Scan open tasks, notify on overdue ones, respect a 30-minute per-task cooldown.
2. **Morning briefing** (`30 3 * * 1-6` — 09:00 IST Mon–Sat). Aggregate the firm's pending work, fetch the day's top compliance news via Serper API, send an HTML digest to the firm admin.
3. **Client chase** (`30 3 28 * *` — 09:00 IST on the 28th). For the next filing period, identify clients with missing documents and ping them, capped at 20 clients per cycle and a 7-day per-client cooldown.
4. **Voice notes**. A user sends an OGG voice message; we transcribe via Whisper and feed the transcript into the existing supervisor flow.

The PRD treats the briefing and chase as Enma's "chief-of-staff" voice — what differentiates the product from a passive bot. They must (a) actually fire on time, (b) be idempotent under retries, (c) survive a crashed scheduler without sending two briefings, and (d) be auditable.

### Forces

- **Two-service split is already paid for.** The gateway is a thin Node.js process that already speaks to the backend via HMAC-signed envelopes. Adding `node-cron` schedulers there costs almost nothing and keeps the backend stateless — exactly the property that lets us run multiple backend replicas later.
- **IST anchoring is non-negotiable.** Indian compliance work is IST-anchored (GSTR-1 11th, GSTR-3B 20th). "9:00 AM" must mean 9:00 AM in Asia/Kolkata regardless of where the gateway VM runs. `now_ist()` already exists in `app/utils/date_utils.py`; the scheduler must use the same zone.
- **Cron has no `message_id`.** The Phase 3 idempotency key is `(chat_id, message_id)`. Cron-spawned envelopes have neither a Telegram message_id nor (in the briefing/chase cases) a single chat_id. We need a second idempotency key that does not collide with user-triggered envelopes.
- **The Serper API is a paid third party.** A misconfigured loop that fires Serper every 5 minutes could quietly burn $100/day. Cost control belongs in the design, not in a runbook.
- **Whisper output is plain text.** Once we have a transcript, the existing supervisor + command pipeline can handle it unchanged — but the pipeline expects an envelope of kind `command`. We need to decide whether voice becomes a wrapper that re-dispatches as a command, or a parallel pipeline.
- **No queue infrastructure (ADR-001 §Decision 3).** We stayed in-process for the worker; the cron tier should stay in-process too unless this phase proves we need otherwise.
- **Supabase transaction-pool mode (port 6543).** Long-lived transactions across a 5-minute heartbeat would be catastrophic. Each cron run opens a short-lived session per side-effect.

---

## Decision Summary

| # | Decision | One-line rationale |
|---|----------|-------------------|
| 1 | **Gateway-side `node-cron` triggers**, not backend self-scheduler. | Keeps backend stateless and matches the existing envelope contract — cron is just one more HMAC-signed POST. |
| 2 | **Cron envelopes use a separate idempotency key:** `(kind, scheduled_at_minute)`. | `(chat_id, message_id)` is undefined for cron; using a deterministic key keeps the same `idempotency_log` table and the same middleware. |
| 3 | **New backend routes under `/worker/cron/*`** with their own dependency, **not** the user-envelope `verify_envelope`. | Cron envelopes have no `chat_id` and a different idempotency key shape; sharing the existing verifier requires ugly branches. A sibling dependency is cleaner and lets the routes stay 401-protected the same way. |
| 4 | **Per-target cooldowns live in dedicated tables**, not in a generic "notification ledger." | `client_notifications.cooldown_until` already exists for client_chase; tasks get `tasks.last_notified_at`. No new schema. |
| 5 | **Voice = wrapper, not parallel pipeline.** Transcribe → synthesize a `command`-kind envelope in memory → invoke `_run_command_pipeline`. | Reuses the entire Phase 6 supervisor + command surface unchanged. One pipeline to maintain, not two. |
| 6 | **Serper calls are bounded and cached.** ≤3 results per briefing, in-memory same-day cache keyed on the search query. | Caps the cost at one Serper call per firm per day in the worst case; multi-firm deployments share the cache. |
| 7 | **IST scheduling via `cron-parser`'s tz argument and `now_ist()` in tests.** | One canonical zone, one canonical clock — no manual UTC arithmetic anywhere. |

---

## Detailed Decisions

### Decision 1: Gateway-side `node-cron` triggers

**Choice:** The gateway's existing process registers three `node-cron` jobs that POST to the backend's `/worker/cron/*` endpoints with HMAC-signed envelopes.

```js
// enma-gateway/src/cron/cron_scheduler.js
import cron from "node-cron";
import { dispatchEnvelopeAsync } from "../dispatch/backend_client.js";
import { buildEnvelope } from "../utils/envelope.js";

function fire(kind) {
  const { outer } = buildEnvelope({
    kind,
    chatId: null,
    messageId: null,
    updateId: null,
    payload: { scheduled_at: new Date().toISOString() },
    secret: config.backend.hmacSecret,
  });
  dispatchEnvelopeAsync(outer);
}

cron.schedule("*/5 * * * *",   () => fire("cron_task_heartbeat"),  { timezone: "Asia/Kolkata" });
cron.schedule("30 3 * * 1-6",  () => fire("cron_morning_briefing"), { timezone: "Asia/Kolkata" });
cron.schedule("30 3 28 * *",   () => fire("cron_client_chase"),    { timezone: "Asia/Kolkata" });
```

**Why not backend self-scheduler (`APScheduler`)?** Three reasons:

1. **Replica safety.** A single-process scheduler in the backend means we cannot run two uvicorn workers — they'd each fire. Locking schemes (DB advisory lock, file lock) work but they're another moving part. The gateway is intentionally single-process; cron lives where it's safe by construction.
2. **Existing infrastructure.** The HMAC envelope + idempotency middleware already exists. We get retry semantics, replay protection, signed payloads — all for free.
3. **Single-source-of-truth for routing.** The architecture doc designates one canonical worker controller (`api/routes/worker.py`) and one ACK pattern. Cron envelopes that flow through the same intake honor that.

**Why not external (cloud-scheduler / cron service)?** Adds a third infra dependency for a deployment that doesn't yet need it. Revisit when the gateway moves off single-VM.

### Decision 2: Cron idempotency key — `(kind, scheduled_at_minute)`

**Choice:** Reuse `idempotency_log` but redefine the unique key for cron-kind envelopes.

The current schema is `UNIQUE(chat_id, message_id)`. For cron we set both to a synthetic constant derived from `(kind, scheduled_at)`:

- `chat_id = 0` (reserved; no Telegram chat has id 0)
- `message_id = floor(scheduled_at_unix_seconds / 60)` — minute resolution

That gives us:

- `*/5 * * * *` → message_id changes every 5 minutes; replays within the same minute dedupe; the `*/5` cadence won't collide.
- `30 3 * * 1-6` → message_id changes daily.
- `30 3 28 * *` → message_id changes monthly.

The middleware needs no change. A `cron_*` envelope carries `scheduled_at` in the payload; the idempotency middleware computes `message_id` from that for cron kinds. User envelopes follow the existing path.

**Alternative considered:** A new `cron_runs` table with `(kind, scheduled_at)` PK. Rejected because it duplicates the `INSERT ... ON CONFLICT` pattern we already have, and the existing `idempotency_log` is already a TimescaleDB-eligible hypertable (Phase 8) — we get the retention story for free.

### Decision 3: Sibling dependency `verify_cron_envelope`

**Choice:** `/worker/cron/*` routes use a parallel dependency, not the existing `verify_envelope`.

The differences are small but real:

| Aspect | User envelope | Cron envelope |
|--------|---------------|---------------|
| `chat_id` | Required int | Always `null` (or `0` after rewrite) |
| `message_id` | Optional int | Synthesised from `scheduled_at` |
| `kind` | `document` / `command` / … | `cron_task_heartbeat` / `cron_morning_briefing` / `cron_client_chase` |
| ACK to Telegram | Always | Never |
| Replay window | 5 minutes | Same — Cron fires every 5 min worst case, so 5 min is a safe window |

`verify_cron_envelope` shares 90% of the code with `verify_envelope` — both verify HMAC, decode the inner JSON, check freshness — but the post-decode validation differs (kinds and required fields). Keeping them sibling functions avoids branching inside one big verifier.

### Decision 4: Cooldowns in existing columns

**Choice:** No new tables.

- **Task heartbeat** uses `tasks.last_notified_at`. Heartbeat picks tasks where `due_at <= NOW() AND status='pending' AND (last_notified_at IS NULL OR last_notified_at < NOW() - INTERVAL '30 minutes')`. After notifying, sets `last_notified_at = NOW()`.
- **Client chase** uses `client_notifications` (already exists). For each candidate client, insert a `(client_id, 'document_chase', cooldown_until = NOW() + INTERVAL '7 days')` row inside a transaction with the Telegram send. Skip candidates with an existing row whose `cooldown_until > NOW()`.

**Concurrency safety:** The heartbeat job's UPDATE is conditional on the cooldown predicate; two concurrent runs each see a fresh tuple, but the row-lock + WHERE-clause guarantees at most one wins. Same for client chase (UNIQUE on `(ca_firm_id, client_id, notification_type)` would be even safer — Phase 8 cleanup task).

### Decision 5: Voice = wrapper, not parallel pipeline

**Choice:**

```python
# app/api/routes/worker.py — _run_voice_pipeline (new in Phase 7)
async def _run_voice_pipeline(envelope: DecodedEnvelope) -> None:
    transcript = await whisper.transcribe(envelope.payload["file_id"])
    await telegram.send_message(envelope.chat_id, italic(f"Heard: {safe_text(transcript)}"))
    # Re-enter the command pipeline with a synthetic envelope.
    synthetic = envelope.model_copy(update={
        "kind": "command",
        "payload": {"text": transcript, "entities": []},
    })
    await _run_command_pipeline(synthetic)
```

**Why?** The supervisor and slash-command paths already do the right thing for free-form text. Building a second pipeline that calls the same tools would mean two places to update for every Phase 6 change. Voice becomes "text with extra steps."

**The transcript echo** matters for two reasons: (a) user trust ("did Enma actually hear that?") and (b) when the user later edits to a typo, they have a referencable message.

**Cost ceiling:** Whisper is billed per second. The voice handler enforces `MAX_VOICE_DURATION_S = 60` (Telegram caps voice notes at 60s anyway; we add a belt-and-braces check on the OGG header).

### Decision 6: Bounded + cached Serper

**Choice:**

- Query: a fixed string `"India GST compliance news"` plus the date (`YYYY-MM-DD`). Same string per day → same cache key.
- Limit: `num=3` (Serper's `num` param), `tbs=qdr:d` (past 24 hours).
- In-memory `dict[date, list[result]]` keyed by the IST date. Cleared at process restart.

A single backend process serving N firms makes one Serper call per day total. Cost: at Serper's $50/2,500 queries pricing, that's ~$0.60/year for the briefing.

If a firm-specific news angle is needed later (e.g. "show notices issued to their state"), the cache key adds the state code. Out of scope for Phase 7.

### Decision 7: IST scheduling

**Choice:**

- Gateway: `node-cron`'s `timezone: "Asia/Kolkata"` option. (`node-cron` ≥ 2.x supports IANA zones.)
- Backend: every `datetime.now(...)` call goes through `app.utils.date_utils.now_ist()`, which is already the canonical IST clock and is mocked in tests.

**Why not store `scheduled_at` in UTC and convert at the edges?** We do — both gateway and backend persist UTC. The *scheduling* is IST; the *storage* is UTC. The conversion is one line at each boundary and `cron-parser` does it for us.

---

## Options Considered

### Option A: Gateway-side `node-cron` (chosen)

| Dimension | Assessment |
|-----------|------------|
| Complexity | Low — three lines per job. |
| Replica safety | Strong — gateway is intentionally single-process. |
| Reuses existing infra | Maximally — HMAC, idempotency, ACK, logging all reused. |
| Test isolation | Easy — fire the underlying coroutines directly in tests. |

### Option B: Backend `APScheduler` with DB advisory lock

| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium — new dep, lock acquisition logic, deadlock risk if lock holder crashes. |
| Replica safety | Medium — works with the lock; fails open if lock store is partitioned. |
| Reuses existing infra | Partial — bypasses the envelope contract, so cron paths take a different shape than user paths. |

Rejected because the gateway already does this for free.

### Option C: External scheduler (AWS EventBridge / Cloud Scheduler)

| Dimension | Assessment |
|-----------|------------|
| Complexity | High at integration time; low at runtime. |
| Vendor lock | High. |
| Scope fit | Wrong for Phase 7 — we're not multi-region yet. |

Revisit in Phase 8+ if we move off single-VM and want stronger at-least-once guarantees than `node-cron` provides.

---

## Trade-off Analysis

**The pivot is "where does the wall clock live."** Choosing Option A puts the wall clock in the gateway, which means: a gateway crash skips a cron tick until restart. We accept that. Mitigation: a small `last_run_at` log inside the gateway lets the next tick detect "we missed N firings" and decide whether to fire-catch-up or skip (Phase 8 enhancement; Phase 7 ships fire-on-tick only).

**Voice-as-wrapper trades a small latency penalty** (transcribe → echo → re-enter pipeline) **for a single canonical pipeline.** The latency is dominated by Whisper anyway (~2–4s for a 30s note); the second hop adds <50ms.

**Serper caching trades freshness for cost.** Once-per-day news is fine for compliance — these aren't breaking news, they're notifications, circulars, and CBDT/CBIC updates that change overnight at most.

---

## Consequences

**Easier:**
- Phase 8 multi-replica backend: cron stays single-fire because it's gateway-driven.
- Adding a new cron job is one `cron.schedule()` line + one route handler.
- Voice-input regressions in supervisor are caught by existing supervisor tests.

**Harder:**
- A gateway crash during cron skips that tick. We need an alert (Phase 8) for "no morning briefing fired in N days."
- The synthetic `(0, minute)` idempotency key requires the docstring on `idempotency.py` to be updated so no one wonders why `chat_id=0` exists.
- Whisper failures (transient API errors) need to either re-enqueue the voice envelope or send a "couldn't transcribe — please retry" message. Phase 7 ships the retry-message path; smart re-enqueueing is Phase 8.

**Revisit:**
- If the briefing becomes per-user (different digests for partner vs. article assistant), Serper caching needs a per-user key — but the budget impact is unchanged because the underlying query stays the same.
- If we move to a queue (Phase 8 perf), cron stays where it is — it pushes the same envelopes into the queue instead of HTTP.

---

## Action Items

1. [ ] **Backend:** add `app/api/middleware/cron_envelope_verify.py` — sibling of `envelope_verify.py`, accepts the three `cron_*` kinds, allows `chat_id=null`.
2. [ ] **Backend:** extend `app/api/middleware/idempotency.py` so cron envelopes derive `message_id = scheduled_at_minute` and `chat_id = 0`. Update docstring.
3. [ ] **Backend:** add `app/api/routes/cron.py` — three POST routes, registered on the same router as `worker.py`.
4. [ ] **Backend:** add `app/agents/briefing.py` — pure-Python digest builder. Pulls from existing query classes (tasks, documents, filings); no LLM call (the briefing is structured data, not prose).
5. [ ] **Backend:** add `app/services/scraper.py` — Serper API client with the in-memory daily cache, ≤3 results, `qdr:d` filter.
6. [ ] **Backend:** add `app/services/whisper.py` — OGG bytes → transcript using the configured `WHISPER_ENDPOINT`. Token-usage logging to the same TimescaleDB hypertable as the LLM client.
7. [ ] **Backend:** extend `app/db/queries/tasks.py` — `list_due_for_heartbeat()` returning overdue tasks whose cooldown has expired; `mark_notified(task_id)` updating `last_notified_at`.
8. [ ] **Backend:** extend `app/db/queries/clients.py` — `list_missing_filing_docs(period)` returning clients with zero documents in the upcoming period.
9. [ ] **Backend:** add `app/db/queries/notifications.py` — `was_recently_chased(client_id)` and `record_chase(client_id, cooldown_until)`.
10. [ ] **Backend:** wire voice → command in `_run_voice_pipeline` inside `app/api/routes/worker.py`. Re-enter `_run_command_pipeline` with a synthetic envelope.
11. [ ] **Gateway:** add `src/cron/cron_scheduler.js` + three job modules. Register in `src/index.js` after the poller starts. All schedules carry `timezone: "Asia/Kolkata"`.
12. [ ] **Gateway:** extend `src/routing/voice_handler.js` (already exists) to pull the OGG `file_id`, attach the duration and mime type, and POST `/worker/voice`.
13. [ ] **Tests:**
    - Cron idempotency: fire same envelope twice in the same minute → second is no-op.
    - Heartbeat cooldown: task notified → second run within 30 min skips.
    - Client chase: 7-day cooldown respected; batch capped at 20.
    - Briefing: mocked Serper returns 3 items → HTML digest contains 3 bullets.
    - Voice: mocked Whisper returns "list overdue tasks" → supervisor's `list_tasks` tool is called.
    - Serper cache: two briefings same day → one Serper call.
14. [ ] **CI gate:** ruff + mypy strict + pytest ≥80% coverage. No warnings.

---

*End of ADR-007.*
