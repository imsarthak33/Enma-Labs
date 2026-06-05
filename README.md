# Enma Labs — Monorepo

**Version:** 0.1.0 (Phase 0 — Foundation)
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
├── scripts/
│   └── init-extensions.sql     # Postgres extension bootstrap
├── enma-gateway/               # Node.js service
├── enma-backend/               # Python service
└── .github/workflows/          # CI pipelines (per service)
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
- [ ] Phase 1 — Database schema & tenant isolation
- [ ] Phase 2 — Gateway Telegram polling & routing
- [ ] Phase 3 — Backend worker & idempotency
- [ ] Phase 4 — Document extraction pipeline
- [ ] Phase 5 — Tax intelligence & firm rules
- [ ] Phase 6 — Identity resolution & supervisor agent
- [ ] Phase 7 — Cron operations & executive functions
- [ ] Phase 8 — Production hardening & launch

## Ground Rules (canonical, enforced)

1. **Decimal everywhere.** All monetary math uses `decimal.Decimal`. `float` for money is a CI failure.
2. **HTML only.** All Telegram messages use HTML parse mode. Markdown is banned.
3. **Tenant scoping is non-negotiable.** Every query goes through `BaseQuery` with explicit `ca_firm_id`.
4. **No phase is skipped.** Each phase ships working, tested code before the next begins.

See [`../ENMA LABS (1)/`](../ENMA%20LABS%20%281%29/) for the canonical product, architecture, security, tech-stack, and implementation documents.
