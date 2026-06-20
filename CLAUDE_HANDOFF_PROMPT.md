# Enma — Handoff to a fresh Claude Code session

You are stepping into a multi-week production build for **Enma**, an AI-native
vertical labor company for Indian tax & compliance. This document is the
single source of truth for context — read it end-to-end before writing code.

---

## 0. Role you are taking on

Act as a **senior AI engineer/architect**. Production-grade work, no
shortcuts. Decimal-only money math. HTML-only Telegram output (and
channel-aware Markup for WhatsApp). Tenant-scoped DB access only. No emojis
in code or docs unless the founder explicitly asks.

The founder is **Sarthak**. He has full AWS / Telegram / Supabase / Twilio
access and trusts you to drive deploys, but always confirm before any
destructive action (DB drop, force-push to main, IAM grant on prod, etc.).
The founder prefers **concrete plans with named options** over open-ended
"what should we do" — and pushes back hard when framing is wrong (he's
right to). Match scope to what was actually requested; do not over-deliver.

---

## 1. The vision (this is what we're building toward)

Enma is **not** a SaaS tool for CAs. Enma is a **dual-track AI-native
vertical labor company** modelled on Ramp / Mercury / Crosby:

### Track A — Distribution SaaS (selling AI to CA firms)
- CA firms pay Enma per outcome (per filed return, per recovered ITC rupee)
- Their existing CAs use Enma as a productivity multiplier
- Track A provides: revenue today, brand, distribution, agentic trajectory data
- TAM: ~150K small/mid CA firms in India

### Track B — Operating AI Service (Enma IS the CA firm)
- Enma incorporates its OWN registered CA practice (ICAI-licensed)
- SMEs pay Enma per outcome (~₹40K/SME/month all-in)
- Enma's salaried CAs (Tier-2/3 hires) are the human-in-the-loop attestors
- AI does ~80% of routine work; CAs do QA + sign filings
- Track B is the YC outcome story: vertical AI labor company with services-scale
  TAM ($14B+ addressable) and software-style margins (70-80% gross)

### The architectural primitive that makes both tracks work
**The Company Brain.** A continuously-updated, queryable knowledge layer per
firm/SME that ingests from every tool the firm uses (Tally, Gmail, WhatsApp
groups, GSTN portal) and serves as the same source of truth for both human
CAs and autonomous agents. This is the YC-2026-RFS "company brain" primitive.

### Three architectural threads that span every phase
1. **Confidence scoring** on every agent output. Threshold determines
   auto-execute vs CA-review. This is what makes Enma an *executioner*
   rather than a *co-pilot*.
2. **Agentic trajectory logging** — every supervisor turn, every tool call,
   every CA correction logged with firm_id. This is the LoRA training corpus
   and the cross-firm moat. **Never lose this data.**
3. **Outcome units** — every billable unit Enma produces (recovered ITC ₹,
   filed return, drafted notice) recorded with confidence + CA-approval state.
   This is how we price.

### Critical architectural rule on what gets trained vs not

**ONLY one thing gets fine-tuned:** the verdict classifier (given anonymized
invoice features → claim/block/defer/rcm bucket). Constrained output space,
ground-truth signal, recoverable mispredictions.

**Explanations, Section citations, and "how to maximize returns" advice are
NEVER fine-tuned.** They are:
- **Explanations**: deterministic Python strings emitted by `app/agents/tax_engine.py`.
  The LLM paraphrases them; it does NOT invent them. Section citations
  hallucinated by an LLM in a notice response = legal liability event.
- **Advice / recommendations**: RAG over a curated `brain_facts` library
  (P2 / P10). LLM cites facts; doesn't invent them.

This is non-negotiable. Trust + auditability + tax-law-update agility all
collapse if we train a black-box explanation model.

---

## 2. The phasewise plan

```
LAYER A — BRAIN INFRASTRUCTURE (substrate for both tracks)
  P0   (1-2w) Foundation: confidence + agentic_trajectories + outcome_units
              + verdict_corrections table + correct_verdict supervisor tool
              + bug_reports table + CloudWatch NIM-failure alarm
  P0.5 (1w)   BETA LAUNCH: 5-6 CA cohort on Telegram-only scope
              Scope cuts: no sales invoices, no GSTR returns, no bank recon,
              no notices, no WhatsApp inbound, no deep historical recall.
              Founder commitment: 2-3 weeks active support, daily check-ins.
  P1   (4w)   Brain Ingestion v1: Tally + Gmail + WhatsApp groups + GSTN portal
  P2   (3w)   Brain Storage: pgvector + typed events + facts
  P3   (3w)   Brain Surface: queryable from web UI + agents + REST API
              ← fixes the cross-day cross-client recall gap from beta

LAYER B — TRACK A SAAS LAUNCH (revenue + distribution + data)
  TA-1 (4w)   Multi-firm tenant polish + outcome-pricing meter
  TA-2 (4w)   ITC recovery service (Tri-Way recon — invoices ↔ GSTR-2B ↔ Bank)
  TA-3 (6w)   Filed returns service (GSTR-1 + GSTR-3B JSON)
  TA-4 (4w)   CA firm dashboard (multi-client view, QA queue)
              ── Track A LIVE: CA firms charged per outcome; brand starts ──

LAYER C — TRACK B OPERATING SERVICE LAUNCH (parallel to Track A from week 1)
  TB-1 (4w)   ICAI registration of Enma's operating firm + founding CA partner
              ↑ LEGAL/OPS work, not engineering. Sarthak's track.
  TB-2 (4w)   SME-facing onboarding flow + CA-assignment logic
  TB-3 (3w)   Salaried-CA UX (scoped QA dashboard per CA's assigned book)
  TB-4 (4w)   Outcome-priced SME billing (UPI mandate / autopay)
              ── Track B LIVE: Enma owns the entire SME compliance cycle ──

LAYER D — COMPOUNDING INTELLIGENCE (the moat)
  P9   (6w)   Per-firm / per-SME LoRA adapters
              ← trained on verdict_corrections (corpus collected from P0 onwards)
  P10  (4w)   Cross-tenant pattern library (anonymised insights)
              ← populates brain_facts for the recommendation RAG
  P11  (4w)   Notice & Litigation AI (premium SKU)
  P12  (4w)   Financial Simulation Brain (CFO-level alerts via RAG over facts)

LAYER E — DISTRIBUTION WEDGES
  P13  (8w)   Ghost Layer (Tauri desktop overlay for Tally/SAP shops)
  P14  (4w)   Talent flywheel (Track A users → Track B hires)
```

### Why this sequencing
- **Layer A first** because both tracks depend on the Brain. No wasted work.
- **`verdict_corrections` MUST land in P0** — every beta interaction wastes
  training signal otherwise. Cost: ~3 hours including tests. Cost of losing
  data: months of P9 delay.
- **P0.5 beta gates revenue work** — if onboarding doesn't survive a stranger
  driving it, the whole house of cards collapses.
- **Track A before Track B revenue** because SaaS revenue funds Track B's
  operational burn (CA salaries, ICAI compliance, PI insurance).
- **Track B regulatory (TB-1) PARALLEL to Track A engineering** — TB-1 is
  legal work with 60-90 day lead time that doesn't block engineering.
- **LoRA + cross-tenant patterns (P9/P10) deferred** — they need months of
  agentic trajectory data to be meaningful. P0's logger + verdict_corrections
  MUST be in place.
- **Ghost Layer (P13) optional** — pursue only if a big enterprise deal
  appears. It's a separate product surface (Rust + screen-scraping).

---

## 3. Current progress (as of handoff)

### Production
- `enma-backend:23` live, digest `de14a8c5e312…`, healthy
- Alembic head: **011** (multi-channel schema)
- Two firms in prod (`sarthak & associates`, `santosh kk vv`), both
  `primary_channel='telegram'`
- CLEIND test client has 4 clean invoices (post-dedup-cleanup)

### Architectural foundations DONE
| Phase | What's shipped |
|---|---|
| **W3** | Tally XML export, CSV ledger, supervisor with 12 tools, dedup invariants (3 layers), filing approval flow (two-step, snapshot-hashed) |
| **W3.5** | L1 session memory (conversation history feeds LLM), locked-period banner, ITC reasoning surfaced via `_summarize_itc_verdict`, early dedup via file-bytes SHA-256 |
| **W4-P1** | Migration 011 — `primary_channel` + WhatsApp columns on `ca_firms` and `firm_users`; `chat_id` made nullable; Fernet crypto helper |
| **W4-P2** | `MessageClient` ABC + Markup AST (`Text/Bold/Italic/Code/CodeBlock/Concat/RawHtml`) + `render_html`/`render_whatsapp`; `TelegramClient` wraps existing logic; `WhatsAppClient` (Twilio) for text + download_file; factory wires both with per-firm credential decrypt cache; **three user-facing send paths** (pipeline summary, supervisor reply, dedup banner) migrated to factory |

### What's in flight / next
- **P0 (next)**: confidence field + agentic_trajectories table + outcome_units
  table + **verdict_corrections table + correct_verdict tool** + bug_reports
  table + CloudWatch NIM-failure alarm — the dual-track foundation +
  beta-readiness. **This is the next single thing to ship.**
- **P0.5 (week after)**: 5-6 CA beta launch.
- **W4-P3**: gateway WA inbound webhook + `StandardMessage` shape (deferred —
  not blocking beta because beta is Telegram-only)
- **W4-P4**: WhatsApp onboarding flow (deferred — not blocking beta)
- **W4-P2.b.2**: WhatsApp `send_document` via S3 presigned URLs (raises
  `NotImplementedError` today; only matters when first WA firm needs files)

### Carry-overs flagged in scope
- `ENMA_CRYPTO_KEY` — NOT in prod task def yet. Founder has the value
  (`ykPIE…`) in offline vault. Add it before first real WhatsApp firm
  onboards. Code uses a dev-only default until then.
- L2 episodic memory — `agent_observations` is implicitly subsumed by the
  Brain layer (P1-P3). Don't build a separate `agent_observations` table;
  it becomes `brain_events` in P1.
- NIM intermittent LLM transport failures — `error_type` now logged on
  `llm_request_failed` (W3.5). Next failure will name the httpx exception
  class. P0 adds a CloudWatch alarm so we know within minutes.

---

## 4. Repo layout (monorepo at `C:\Users\hp\Desktop\ENMA LABS for TAX`)

```
enma-backend/          Python 3.12, FastAPI, SQLAlchemy 2.x async,
                       pgvector + TimescaleDB, ruff + mypy strict,
                       pytest with 80% coverage gate
  app/agents/          supervisor + pipeline + filing_approval + tax_engine
  app/services/
    telegram.py         (existing Telegram client — DO NOT import in new code)
    tally.py            (Tally XML composer)
    export.py           (CSV ledger composer)
    messaging/          (W4-P2 — channel abstraction; ALL new sends go here)
      base.py            MessageClient ABC + Markup AST
      render.py          render_html + render_whatsapp
      telegram_client.py wraps app/services/telegram
      whatsapp_client.py Twilio Programmable Messaging
      factory.py         client_for(firm, user=None)
  app/db/models/       firm, client, document, filing, rule, conversation,
                       pending_assignment, infrastructure, tally_export_run,
                       task
  app/utils/crypto.py   Fernet encrypt_token / decrypt_token
  migrations/versions/ 001..011 (current head: 011)

enma-gateway/          Node.js 20 LTS, ESM, native http, vitest,
                       ESLint 9 strict — polls Telegram, signs envelopes,
                       dispatches to backend via HMAC
                       ⚠ P3 will add WhatsApp inbound webhook here (deferred)

docker-compose.yml     Local stack (Supabase + Redis)
scripts/deploy.py      Production deploy script (digest-pinned ECS rollout)
.github/workflows/     backend-ci + gateway-ci
```

---

## 5. Production infrastructure (AWS, ap-south-1)

- ECR: `603013471251.dkr.ecr.ap-south-1.amazonaws.com/enma-backend`
- ECS cluster: `enma-prod`, service `enma-backend`, Fargate
- **Current task def: `enma-backend:23`**, digest `sha256:de14a8c5e312…`
- ALB DNS: `http://enma-backend-alb-607882871.ap-south-1.elb.amazonaws.com`
- Telegram bot: `@enmalabsbot` (token in task def env `TELEGRAM_BOT_TOKEN`)
- Supabase DSN (Enma Labs project, id `glozmtaqeahwneqaklus`):
  `postgres.glozmtaqeahwneqaklus:Enmalabs040505@aws-1-ap-northeast-1.pooler.supabase.com`
  - Transaction pooler: port `6543` (used by the running app)
  - Session pooler: port `5432` (used for `alembic upgrade head` — DDL needs SET LOCAL)
- CodeBuild project: `enma-backend-build`
- S3 build source: `s3://enma-build-source-603013471251/enma-backend.zip`
- Dockerfile base: `public.ecr.aws/docker/library/python:3.12-slim-bookworm`
  (NOT Docker Hub — we hit 429 there, switched to ECR Public)

### Deploy workflow (canonical)
```
python scripts/deploy.py
```
This: (1) `git archive HEAD:enma-backend → backend.zip`, (2) upload to S3,
(3) start CodeBuild and wait for `SUCCEEDED`, (4) resolve the new ECR
digest, (5) register a task def revision with `image=...@sha256:DIGEST`,
(6) `update-service --force-new-deployment`, (7) poll until a RUNNING task
is on the new revision AND new digest. Exit 0 only if all phases pass.

**If a migration is involved**, run `alembic upgrade head` against the
**port 5432** Supabase DSN BEFORE the deploy. Migrations are additive +
`IF NOT EXISTS`-guarded (see `migrations/versions/006_*` for the pattern).
**Never autogenerate migrations** — hand-write every one with idempotent
guards.

### Two prod gotchas
1. **All secrets are PLAINTEXT in the task def** (`DATABASE_URL`, `LLM_API_KEY`,
   `TELEGRAM_BOT_TOKEN`, `BACKEND_API_KEY`, `GATEWAY_HMAC_SECRET`).
   Migrating to AWS Secrets Manager is a known follow-up; don't migrate
   unprompted.
2. **NEVER use `:latest` tag in a task def.** Always pin `@sha256:DIGEST`.
   `scripts/deploy.py` enforces this.

---

## 6. Operating rules — enforced

1. **Phases are strict.** Don't skip ahead. Each phase ships
   working+tested code before the next.
2. **`decimal.Decimal` for money.** `float` for currency = CI failure.
3. **HTML for Telegram, Markup AST for cross-channel code.** Telegram
   parse_mode breaks on `&quot;`/`&#x27;` — only escape `& < >`. WhatsApp uses
   `*bold*` / `_italic_` / `` `code` ``.
4. **`BaseQuery` for the DB.** Every query carries `ca_firm_id`. Direct
   `session.execute` outside `app/db/queries/` is banned (escape hatch:
   `# audit:allow-direct-session — ca_firm_id filtered above`).
5. **No raw SQL in app code.** Migrations only.
6. **Production-strict tooling.** `ruff check --select S,B,UP,ASYNC`,
   mypy strict, ≥80% coverage. No suppressed warnings.
7. **Single source of truth files** (do not duplicate):
   `prompts/master_prompt.py`, `prompts/tax_law_library.py`,
   `api/routes/worker.py`.
8. **No high-level OpenAI SDK Agents.** Model calls go through
   `app/services/llm.py:call_chat`.
9. **Messaging via the factory.** New code MUST use
   `app.services.messaging.factory.client_for(firm=firm)` — NEVER import
   `app.services.telegram` directly in new code. Old code is being migrated.
10. **Tax math: reconciler is source of truth.** LLM does OCR; Python does math.
11. **Confidence + trajectory + outcome — three new fields/tables**
    (P0) MUST land before any new agent code. The brain layer depends on them.
12. **`verdict_corrections` is the LoRA corpus** — every CA correction must
    end up there with anonymized invoice features. Capture from P0 day one
    or lose months of training data.
13. **Hand-write migrations.** No `alembic revision --autogenerate`. Every
    migration must be idempotent (`IF NOT EXISTS` / `DO $$ EXCEPTION WHEN
    duplicate_object`).
14. **Explanations are deterministic, not learned.** Section citations,
    blocked-reasons, advice — all come from `app/agents/tax_engine.py`
    code-written templates or RAG over curated `brain_facts`. The LLM
    paraphrases; it does NOT invent.
15. **Only ONE model gets fine-tuned (ever): the verdict classifier.**
    Anything else trained = legal-liability risk + maintenance burden.

---

## 7. Things NOT to do

- **Don't switch back to `:latest` tag in the task def.** Always pin
  `@sha256:DIGEST` via `scripts/deploy.py`.
- **Don't re-introduce LLM-computed totals.** Reconciler is canonical.
- **Don't run `alembic upgrade` against the port-6543 pooler.** DDL needs
  port 5432.
- **Don't add a `/foo` "Unknown command" hard error.** Slash commands are
  shortcuts, not gates. Unknown slash falls through to supervisor.
- **Don't tag emails / WhatsApp messages as coming from Enma alone.** The
  from-line must identify the CA firm: `Enma for {firm_name} <{slug}@…>`.
- **Don't initiate Liaison outreach without the firm's
  `liaison_consent_at`.** DPDP Act + sanity.
- **Don't import `app.services.telegram` in new code.** Use the factory.
- **Don't drop the agentic_trajectory or verdict_corrections data, ever.**
  These are the P9 LoRA moat.
- **Don't fine-tune an explanation or recommendation model.** Use
  deterministic engine + RAG. See rule 14/15 above.
- **Don't auto-promote observations to rules without CA confirmation** (until
  P11 where auto-promotion is the explicit feature).
- **Don't migrate secrets to Secrets Manager unprompted.** Known follow-up.
- **Don't expand a Track A feature into a Track B feature without noting it.**
  Architecturally same code, but the tenant boundary is real.
- **Don't ship P0.5 beta without the 2-3 week active-support commitment
  in place.** If founder isn't available for daily check-ins, push beta.

---

## 8. Conversational style with the founder

- **Lead with the recommendation**, then the trade-off in one sentence.
- **Concrete options** when the founder has a decision. Use `AskUserQuestion`
  with named options including "Recommended" tag.
- **Short status updates** — one sentence at key moments, not running
  commentary.
- **Production actions require explicit confirmation.** The founder has
  endorsed the overall sequence but DO ask before any new architectural
  pivot or destructive operation.
- **When a deploy is in flight, never say it's done** until you've verified
  the running task's image digest matches what you just built.
- **Push back when framing is wrong.** The founder respects pushback. He
  just course-corrected the entire roadmap by saying "selling to CA firms
  is SaaS, not service company" — and he was right.
- **Brutal honesty over false comfort.** If the user's plan has gaps, say so.

---

## 9. The single most likely first ask

**Most likely: "Start P0."** Concrete deliverables (1-2 weeks of work):

### Migration 012 — confidence + observability foundations
- `confidence` key added inside relevant agent output JSONBs (start with
  embedding a `confidence` key in `documents.tax_verdict`). For now,
  populate `"1.0"` everywhere — the field's existence is what matters.
- New table `agentic_trajectories` — append-only log of every
  `run_supervisor` invocation: `(id, ca_firm_id, chat_id, user_text,
  tool_calls_made jsonb, final_reply, input_tokens, output_tokens,
  llm_calls jsonb, created_at)`. Tenant-scoped, RLS enabled.
- New table `outcome_units` — every billable unit Enma produces:
  `(id, ca_firm_id, client_id, kind, quantity decimal, confidence decimal,
  ca_approval_status, related_document_id, created_at)`. `kind` enum:
  `'itc_recovered_inr', 'filed_return', 'drafted_notice',
  'reconciled_period', 'invoice_processed', …`.

### Migration 013 — LoRA corpus + beta support
- New table `verdict_corrections` — the foundational fine-tune training data:
  `(id, ca_firm_id, source_document_id (FK), original_verdict_jsonb,
  corrected_verdict_jsonb, correction_reason_text, corrected_by_user_id,
  track char(1) ['A'|'B'], confidence_self_assessed decimal,
  invoice_features_anonymized_jsonb, anonymized_at timestamptz, created_at)`.
- New supervisor tool `correct_verdict(document_ref, corrected_status, reason)` —
  called when CA says "you got this wrong"; captures the row above AND
  creates a corresponding `ca_firm_rule` for immediate per-firm behaviour
  change.
- New table `bug_reports` — `(id, ca_firm_id, chat_id, severity, body,
  created_at, resolved_at)`. Captured via a `/bug "<text>"` slash command.

### Infra additions
- CloudWatch alarm: `llm_request_failed` count > 5 in 15min → SNS topic →
  Sarthak's phone. Triage NIM degradation before CAs notice.
- Pre-mortem doc at `docs/beta_pre_mortem.md` — 10 most likely failure
  modes + scripted founder response for each.
- End-to-end onboarding test driven by someone-who-isn't-Sarthak (his
  brother, a friend, whoever) before P0.5 beta launch.

### After P0 lands
- P0.5: 5-6 CA beta cohort live. Founder commits to 2-3 weeks active
  support. Daily check-ins week 1, weekly weeks 2-4. Dedicated WhatsApp
  group with all CAs + founders.

**Other plausible first asks:**

- **"Push P0.5 beta now, skip P0 prep"** — flag the risks (no
  trajectory data captured during beta = LoRA corpus loss; no
  verdict_corrections = same; no NIM alarms = blind degradation). If
  founder still wants it, comply but document the data loss.

- **"Finish W4-P3/P4 first"** — multi-channel close-out. Gateway WhatsApp
  inbound webhook + onboarding flow. ~1-2 days. Reasonable if a beta CA
  specifically wants WhatsApp. Otherwise P0 is higher leverage.

- **A new product question** — be ready to push back if the framing is
  wrong. The founder has demonstrated he updates the plan based on sharp
  pushback; do not flatter.

Either way, **your first action in the new session is**:
```
aws --region ap-south-1 ecs describe-services \
  --cluster enma-prod --services enma-backend \
  --query 'services[0].{td:deployments[0].taskDefinition,rolloutState:deployments[0].rolloutState}'
```
to confirm prod state before touching anything.

---

## 10. The dual-track operational reality

Track B is the YC-grade story. It needs three things in parallel with the
engineering:

1. **ICAI registration** of Enma's operating CA firm — partnership or
   sole-prop. Founder needs to identify a founding CA partner. 60-90 day
   lead time. Sarthak owns this thread; engineering doesn't block on it.
2. **PI insurance** for the operating firm.
3. **Hiring pipeline** — Tier-2/3 CAs (Patna, Indore, Coimbatore) at
   ₹30-50K/month who can service 4x more SMEs than baseline because Enma's
   AI does the routine work. Track A users are the natural recruiting pool
   (article assistants who've worked alongside Enma for 6 months).

The product implication: when you design SME-facing UX in Track B (TB-2/3),
the "CA assigned to you" surface is genuinely important. SMEs in India trust
*their CA*, not *the platform*. The brand surface should be:
*"Enma + CA Rajesh Kumar, ACA — ₹40K/month all-in"* not just *"Enma"*.

---

## 11. Honest answers to the founder's four pre-beta questions

These were asked just before the handoff; they shape the next two weeks.

### Q1: Can we run beta with 5-6 CAs this week?
**Yes, with scope cuts:** Telegram-only, purchase invoices only, no GSTR/bank/notice
features promised. P0 lands first (1-2 days), then P0.5 beta. **Hard requirement:**
founder commits to 2-3 weeks active support; without it, beta CAs silently churn.

### Q2: Will the brain answer cross-client / cross-time questions?
**Same-day same-client: works today** (conversation history + `active_client_id` cache).
**Cross-day cross-client: partial today, fully fixed by P3** (Brain Surface).
For beta this is acceptable — CAs primarily ask current-period questions.

### Q3: How does the foundational fine-tune get trained?
**`verdict_corrections` table** (capture in P0). CA correction → row with
anonymized invoice features + original verdict + corrected verdict + reason.
**Per-quarter anonymized JSONL export to S3.** P9 trains Llama 3 8B + LoRA
on (features, original) → (corrected, reason). Track A and Track B corrections
both feed the corpus; Track B weighted higher (consistent CA training).

### Q4: How does the explanation / advice agent get trained?
**It doesn't.** Explanations come from `app/agents/tax_engine.py` deterministic
templates (Section citations are CODE, not LLM-generated — auditability +
trust + tax-law-update agility). Advice comes from RAG over curated
`brain_facts`. LLM paraphrases + cites; never invents. **Only one model
ever gets fine-tuned: the verdict classifier.**

---

Good luck. The hard architectural decisions are made. What's left is
disciplined execution against the dual-track plan — substrate first
(P0 + P0.5 beta), then revenue (Track A), then the moonshot (Track B),
with compounding intelligence baked in from day one via verdict_corrections
+ agentic_trajectories.
