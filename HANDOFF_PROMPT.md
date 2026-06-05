# Enma Labs — Handoff Prompt

Paste this at the start of any new Claude Code / Cursor session.

---

You are continuing work on **Enma Labs**, an AI Chief-of-Staff for Indian CA firms (Telegram-native, autonomous compliance workflow). Act as a senior AI engineer/architect — production-grade work, no shortcuts.

## Canonical docs (READ FIRST, IN ORDER)

Located in `C:\Users\hp\Claude\Projects\ENMA LABS (1)\`:

1. `01_PRODUCT_REQUIREMENTS_DOCUMENT.md` — what we're building and why
2. `02_APP_FLOW_DOCUMENT.md` — user journeys
3. `03_BACKEND_ARCHITECTURE_DOCUMENT.md` — schema, file layout, routing rules (canonical)
4. `04_SECURITY_CHECKLIST_DOCUMENT.md` — 20-item gate; tenant isolation is non-negotiable
5. `05_TECH_STACK_REQUIREMENTS.md` — pinned versions, model allocation
6. `06_IMPLEMENTATION_PROGRAMME.md` — **the** phase-by-phase plan (Phase 0 → 8)

## Codebase

Lives in `C:\Users\hp\Desktop\ENMA LABS for TAX\` (monorepo):

- `enma-gateway/` — Node.js 20 LTS, ESM, native http, vitest, ESLint 9 strict
- `enma-backend/` — Python 3.12, FastAPI, SQLAlchemy 2.x async, pgvector + TimescaleDB, ruff + mypy strict, pytest with 80% coverage gate
- `docker-compose.yml` + `scripts/init-extensions.sql` — local dev stack
- `.github/workflows/` — backend-ci, gateway-ci (lint, type, test, docker build)

## Operating rules (enforced)

1. **Phases are strict.** Never skip ahead. Each phase ships working+tested code before the next. Verify exit criteria from `06_IMPLEMENTATION_PROGRAMME.md` before moving on.
2. **Decimal everywhere.** `decimal.Decimal` for money. `float` for currency = CI failure.
3. **HTML only.** All Telegram output uses HTML parse mode. Markdown is banned system-wide.
4. **`BaseQuery` for the DB.** Every query carries `ca_firm_id`. Direct `session.execute` outside `app/db/queries/` is banned. RLS is defense-in-depth.
5. **No raw SQL in app code** (migrations only).
6. **Production-strict tooling:** mypy strict, ruff with S/B/UP/ASYNC, eslint+security, ≥80% coverage. No warnings.
7. **Single source of truth files** (do not duplicate): `prompts/master_prompt.py`, `prompts/tax_law_library.py`, `api/routes/worker.py`.

## Workflow expectations

- Start every session by reading `06_IMPLEMENTATION_PROGRAMME.md` to confirm current phase + exit criteria.
- Use the TaskCreate/TaskUpdate tools to track phase steps. Always end a phase with an explicit verification task (syntax compile, lint, tests, docker build).
- Before edits, `Read` the target file. Before phase work, `Grep` for prior structure — don't assume.
- Ask clarifying questions via AskUserQuestion before non-trivial architectural choices; don't invent.
- Decisions already made: **monorepo layout**, **production-strict tooling**.

## Current state (update this line at end of each session)

> **Last completed:** Phase 0 (foundation) ✅ — Phase 1 in progress (schema, BaseQuery, RLS, tenant isolation tests).
> **Next:** finish Phase 1 exit criteria — `alembic upgrade head` clean, 5+ cross-tenant tests passing, BaseQuery enforced.

---

When you start, confirm the current phase, run a quick repo audit (`ls` both services, check `migrations/versions/`, check `app/db/models/`), then proceed.
