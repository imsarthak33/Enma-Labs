# Enma Labs — Monorepo

**Version:** 0.8.0 (Phase 8 — Production Hardening)
**Status:** Active development

Enma is an autonomous Chief of Staff for Indian Chartered Accountant firms — a Telegram-native AI system that ingests documents, computes tax verdicts deterministically, and produces filing-ready outputs.

This monorepo contains two services that together form the platform:

| Service | Stack | Role |
| --- | --- | --- |
| [`enma-gateway`](./enma-gateway/) | Node.js 20 LTS | Telegram polling, event routing, cron, media buffering. Zero business logic. |
| [`enma-backend`](./enma-backend/) | Python 3.11+, FastAPI, SQLAlchemy 2.x, PostgreSQL 16 + pgvector + TimescaleDB | All business logic: extraction, tax computation, RAG, supervisor agent. |

## Repository Layout

```
.
├── docker-compose.yml          # Local dev orchestration
├── docker-compose.prod.yml     # Production-like local validation
├── .env.production.example     # Production env template (NEVER commit populated copy)
├── scripts/
│   ├── init-extensions.sql     # Postgres extension bootstrap
│   └── security_audit.py       # 20-item security checklist runner (gates CI)
├── perf/                       # Locust load-test harness + budget assertions
├── monitoring/                 # Dashboard SQL views + Grafana JSON
├── docs/
│   ├── DEPLOYMENT.md           # Production deploy + rollback
│   ├── ONBOARDING.md           # New-firm onboarding playbook
│   └── SECRET_ROTATION.md      # Quarterly key rotation procedure
├── enma-gateway/               # Node.js service
├── enma-backend/               # Python service
└── .github/workflows/          # CI pipelines (per service + security audit)
```

## Quick Start

Prerequisites: Docker 25+, Docker Compose 2.24+.

```bash
cp .env.example .env
# Fill in TELEGRAM_BOT_TOKEN (a placeholder is fine for Phase 0).
docker compose up --build
```

Then:

- Gateway health: `curl http://localhost:3000/health` → `{"status":"ok",...}`
- Backend health: `curl http://localhost:8000/health` → `{"status":"ok",...}`
- Postgres: `psql postgres://enma:dev_password@localhost:5432/enma_dev`

## Phase Status

Tracking against [`06_IMPLEMENTATION_PROGRAMME.md`](../ENMA%20LABS%20%281%29/06_IMPLEMENTATION_PROGRAMME.md):

- [x] **Phase 0 — Foundation:** Repos scaffolded, Docker Compose, health checks, CI.
- [x] **Phase 1 — Database schema & tenant isolation:** All tables, BaseQuery, RLS, isolation tests.
- [x] **Phase 2 — Gateway Telegram polling & routing:** Dedup, media buffer, envelope dispatch.
- [x] **Phase 3 — Backend worker & idempotency:** HMAC verify, idempotency log, ack messages.
- [x] **Phase 4 — Document extraction pipeline:** Classifier → extractor → verifier with Decimal math.
- [x] **Phase 5 — Tax intelligence & firm rules:** Tax engine, hybrid RAG, correction feedback loop.
- [x] **Phase 6 — Identity resolution & supervisor agent:** Five-stage cascade, supervisor ReAct, filing approval.
- [x] **Phase 7 — Cron operations & executive functions:** Briefings, task heartbeat, client chase, voice.
- [x] **Phase 8 — Production hardening & launch:** Sentry scrubbing, security audit CI, perf harness, monitoring, prod manifests.

## Ground Rules (canonical, enforced)

1. **Decimal everywhere.** All monetary math uses `decimal.Decimal`. `float` for money is a CI failure.
2. **HTML only.** All Telegram messages use HTML parse mode. Markdown is banned.
3. **Tenant scoping is non-negotiable.** Every query goes through `BaseQuery` with explicit `ca_firm_id`.
4. **No phase is skipped.** Each phase ships working, tested code before the next begins.

See [`../ENMA LABS (1)/`](../ENMA%20LABS%20%281%29/) for the canonical product, architecture, security, tech-stack, and implementation documents.

## Phase 8 operational tooling

- **Security audit** — `python scripts/security_audit.py` runs the 20-item
  checklist locally and as a CI gate. See [04_SECURITY_CHECKLIST_DOCUMENT.md](../ENMA%20LABS%20%281%29/04_SECURITY_CHECKLIST_DOCUMENT.md).
- **Perf harness** — `perf/locustfile.py` exercises `/worker/*` under the
  Phase 8 budget (50 concurrent uploaders, 10 simultaneous extractions).
  `perf/assert_budget.py` gates the budget in CI.
- **Monitoring** — `monitoring/dashboard_views.sql` ships the Grafana view
  set; `monitoring/grafana_dashboard.json` is the drop-in dashboard.
- **Deployment** — see [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for the
  build → push → migrate → smoke runbook.
- **Onboarding** — see [`docs/ONBOARDING.md`](docs/ONBOARDING.md) to bring
  up a new CA firm.
- **Secret rotation** — see [`docs/SECRET_ROTATION.md`](docs/SECRET_ROTATION.md).
