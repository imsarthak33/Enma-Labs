<div align="center">

<img src="https://img.shields.io/badge/Enma%20Labs-Open%20Source%20AI%20Native-black?style=for-the-badge&labelColor=000000&color=6366f1" />
<img src="https://img.shields.io/badge/Product-Enma%20for%20Tax-black?style=for-the-badge&labelColor=000000&color=10b981" />
<img src="https://img.shields.io/badge/Status-Active%20Development-black?style=for-the-badge&labelColor=000000&color=f59e0b" />

</div>

---

# Enma Labs

**We believe every professional firm deserves an AI that works as hard as they do.**

Enma Labs is an open-source, AI-native company. We don't build software that automates clicks. We build systems that understand domains — deeply, deterministically, and autonomously — and we open-source everything.

Our first domain: **Indian taxation.**

---

## The Problem

India has 100,000+ Chartered Accountant firms. Each one drowns every month in the same cycle — chasing clients for bank statements, cross-referencing GSTR-2B against purchase registers, computing ITC eligibility, catching reversals before the deadline — all done manually, in Excel, by humans who should be doing higher-order work.

The tools that exist today are portals, not intelligence. They store data. They don't reason about it.

**We think that's wrong. And we built the alternative.**

---

## Enma for Tax

> *"An autonomous Chief of Staff for Chartered Accountant firms — a Telegram-native AI that ingests documents, reconciles GST data deterministically, chases clients proactively, and delivers filing-ready outputs. Without being asked."*

This is not a chatbot. It's not an Excel plugin. It's not a "smart dashboard."

It's an agent. It runs. It watches. It reconciles. When something is missing, it goes and gets it. When a filing deadline approaches, it has already done the work.

Here is what it does — concretely:

| Capability | How it works |
|---|---|
| **Document ingestion** | Client sends a bank statement PDF to their 1:1 Telegram chat or forwards it to their ingest email. Enma receives it, parses it, lands it in the brain. |
| **GST invoice extraction** | Upload any GST invoice. OCR + field extraction in seconds. GSTIN validated. Tax math in `Decimal`. |
| **Tri-Way ITC Reconciliation** | Cross-references invoice ledger ↔ GSTR-2B ↔ bank transactions. Flags irrecoverable ITC and 180-day reversal risk. Deterministic verdict, not probabilistic. |
| **Automated GSTR-2B pull** | On the 15th of every month, Enma pulls GSTR-2B from the GST portal via GSP API for every client who has given consent. No manual download. |
| **Bank statement auto-ingest** | CAs forward client statements from their own inbox to a per-client ingest address. Enma parses, routes, reconciles — automatically. |
| **Proactive client chase** | Before every filing deadline, Enma checks what's missing per client, and messages them directly — on Telegram 1:1, or WhatsApp (Twilio). |
| **Monthly digest** | On the 15th at 9:00 AM IST, the CA receives a per-client ITC recovery + reversal-risk summary. No setup. No prompt. |
| **Supervisor agent** | The CA talks to Enma in plain language. 23 tools available. Query the brain, run recon, approve filings, export ledgers. |

All of this runs on two ECS Fargate services. The CA interacts entirely through Telegram.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Telegram / WhatsApp                         │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                    ┌───────────▼───────────┐
                    │    enma-gateway        │   Node.js 20 LTS
                    │  Telegram polling      │   Event routing
                    │  Cron scheduler        │   HMAC-signed envelopes
                    │  Media buffering       │   Zero business logic
                    └───────────┬───────────┘
                                │  signed envelope
                    ┌───────────▼───────────┐
                    │    enma-backend        │   Python 3.12 + FastAPI
                    │  Supervisor agent      │   SQLAlchemy 2.x async
                    │  Document pipeline     │   pgvector + TimescaleDB
                    │  Tax engine            │   Alembic migrations
                    │  Tri-Way recon         │   Supabase PostgreSQL
                    │  Provider adapters     │
                    └───────────┬───────────┘
                                │
              ┌─────────────────┼─────────────────┐
              │                 │                 │
    ┌─────────▼──────┐  ┌───────▼──────┐  ┌──────▼───────┐
    │  GSTN / GSP    │  │  IMAP inbox  │  │   Twilio     │
    │  GSTR-2B pull  │  │  Bank ingest │  │  WhatsApp    │
    └────────────────┘  └──────────────┘  └──────────────┘
```

### The Company Brain

Every document — invoice, bank statement, GSTR-2B — lands in a single `brain_events` table. Typed by source. Immutable. This is the single source of truth from which every verdict, reconciliation, and report is derived.

The architecture is intentional: **data acquisition is separated from intelligence**. New sources plug in as provider adapters. The reconciliation engine doesn't care where the data came from.

### The Drop-In-Key Pattern

Every external integration — IMAP, GSP, Twilio — ships in the codebase **fully built and dormant**. A `NullClient` returns no-ops when unconfigured. Paste a key into the env, and the real client activates. Zero code changes. This is how a two-person team ships integrations without breaking production.

---

## Repository Layout

```
.
├── enma-gateway/               # Node.js — Telegram polling, cron, routing
│   ├── src/
│   │   ├── cron/               # IST-aware cron scheduler
│   │   ├── utils/              # HMAC envelope signing
│   │   └── bot/                # Telegram webhook + polling
│   └── package.json
│
├── enma-backend/               # Python — all intelligence
│   ├── app/
│   │   ├── agents/             # Supervisor + onboarding handlers
│   │   ├── api/routes/         # FastAPI routes (worker, cron, health)
│   │   ├── db/
│   │   │   ├── models/         # SQLAlchemy ORM models (19 tables)
│   │   │   ├── queries/        # Typed query classes (BaseQuery, tenant-scoped)
│   │   │   └── migrations/     # Alembic (head: 020)
│   │   ├── services/
│   │   │   ├── providers/      # Drop-in-key: email_ingest, gsp, twilio
│   │   │   ├── auto_ingest.py  # Acquisition-agnostic ingest seam
│   │   │   └── client_channels.py  # ADR-017 deep-link + ingest email
│   │   ├── tax/                # Deterministic tax engine (Decimal everywhere)
│   │   └── formatting/         # Telegram HTML (self-escaping)
│   └── tests/                  # 200+ tests across services, agents, api
│
├── docs/
│   ├── adr/                    # Architecture Decision Records
│   │   └── ADR-017-client-ingestion-channel.md
│   ├── DEPLOYMENT.md
│   └── ONBOARDING.md
│
├── docker-compose.yml          # Local dev
└── scripts/
    ├── deploy.py               # Backend: git archive → S3 → CodeBuild → ECS pin
    └── deploy_gateway.py       # Gateway: same pattern
```

---

## What's Live in Production

| Phase | Feature | Status |
|---|---|---|
| 0 | Substrate: brain_events, outcome_units, alarms | ✅ live |
| 1 | Company Brain (Layer A): Tally ingest, query_brain, tax-KB | ✅ live |
| 2 | Tri-Way ITC Reconciliation + PDF e-statement parser | ✅ live |
| 3 | Outcome Meter: recovered ITC + reversal risk ledger | ✅ live |
| 4 | Robustness: pending-resume, deploy scripts, escaping fixes | ✅ live |
| 5 | Proactive Reporting: monthly digest cron (15th, 09:00 IST) | ✅ live |
| 6 | Provider-Adapter Foundation: drop-in-key architecture | ✅ live |
| 7a | Bank-via-email: IMAP ingest → auto recon | ✅ built · dormant until creds |
| 7b | GSTR-2B via GSP: monthly pull cron | ✅ built · dormant until GSP key |
| 8a | Per-client 1:1 deep-link binding (ADR-017) | ✅ live |
| 8b | Completeness ledger (what's arrived vs. required) | 🔨 next |
| 8c | Filing-period-aware chase cron | 🔨 next |
| 9 | GSTR-1/3B filing (TA-3) | ⬜ planned |
| 10 | Track B: Enma as its own operating CA firm | ⬜ planned |

**Production:** AWS ECS Fargate, ap-south-1 · Backend rev 47 · Gateway rev 17 · Alembic `020`

---

## Quick Start (Local Dev)

```bash
# Prerequisites: Docker 25+, Docker Compose 2.24+
cp .env.example .env
# Set TELEGRAM_BOT_TOKEN (a test bot is fine for local dev)

docker compose up --build
```

Verify:
```bash
curl http://localhost:3000/health   # → {"status":"ok"}
curl http://localhost:8000/health   # → {"status":"ok"}
psql postgres://enma:dev_password@localhost:5432/enma_dev
```

Run tests:
```bash
cd enma-backend
pip install -e ".[dev]"
pytest tests/ -x -q
```

---

## Engineering Principles

These are not aspirations. They are enforced by CI.

**1. Decimal everywhere.**
All monetary math uses `decimal.Decimal`. A `float` for money is a CI failure. GST amounts are exact or they are wrong.

**2. Deterministic verdicts.**
Tax computations have no probability distribution. A ₹50,000 ITC claim is either recoverable or it isn't. The engine computes this from statute, not from a language model.

**3. Tenant isolation is non-negotiable.**
Every query goes through `BaseQuery` with an explicit `ca_firm_id`. Cross-firm data access exists in exactly two places (marked with comments specifying which caller is allowed), and those places are grep-checked.

**4. The brain never forgets.**
`brain_events` is append-only. Corrections live alongside originals as new events. You can always replay the full history of what Enma knew and when.

**5. HTML only.**
All Telegram messages use HTML parse mode. Markdown is banned. The formatter self-escapes — `bold(x)` never double-escapes, even when `x` contains `&`.

---

## ADR Log

| ADR | Decision |
|---|---|
| [ADR-017](docs/adr/ADR-017-client-ingestion-channel.md) | Client document ingestion: 1:1 deep-link + per-client ingest email. Groups rejected (WhatsApp Business API blocks bot-in-group). |

---

## Roadmap & Vision

**Enma for Tax is the first instantiation of a pattern we intend to repeat.**

The insight is simple: every professional domain has the same shape — experts drowned in process that computers could handle, clients who don't send things on time, and regulators who want specific forms in specific formats by specific deadlines. The domain knowledge is deep but finite. The workflows are repetitive but critical.

We are building the infrastructure to turn domain knowledge into autonomous agents. Tax is the proof-of-concept. The architecture — Brain, Provider adapters, Supervisor agent, Cron-driven proactive behavior — is designed to be domain-portable.

**What's next from Enma Labs:**
- Enma for Legal (contract review, compliance tracking)
- Enma for Audit (workpaper automation, sampling, sign-off)
- Enma for Finance (MIS, cash-flow forecasting, board reporting)

Each new product gets the same substrate. Different domain knowledge. Same autonomous agent pattern.

---

## Contributing

This repository is open-source under the MIT License.

If you are a CA firm, a GST practitioner, a tax engineer, or an AI researcher who cares about getting domain automation right — read the code, open issues, send PRs.

The principles above are enforced by CI, but the domain is wide open. We especially welcome:
- GSP integration improvements and real-world OTP consent flows
- New bank statement parsers (we have SBI, HDFC, ICICI — add yours)
- Tax engine edge cases and statute citations
- Account Aggregator (Sahamati AA) integration

---

## The Team

**Enma Labs** — building AI-native professional infrastructure, open-source, from India.

> *"The people who are crazy enough to think they can replace the compliance stack are the ones who do."*

---

<div align="center">

**[GitHub](https://github.com/imsarthak33/Enma-Labs)** · **[Docs](docs/)** · **[ADRs](docs/adr/)**

</div>
