# enma-backend

Python service that owns all business logic for Enma Labs: LLM orchestration, tax computation, RAG over firm rules, identity resolution, supervisor agent, and database access.

## Stack

- Python 3.11+ (3.12 preferred)
- FastAPI 0.110+ on Uvicorn (Gunicorn + UvicornWorker in prod)
- SQLAlchemy 2.x async + asyncpg + Alembic
- PostgreSQL 16 with pgvector, TimescaleDB, pg_trgm
- structlog for JSON logging, Sentry for error capture

## Layout (Phase 0 baseline)

```
enma-backend/
├── pyproject.toml
├── Dockerfile
├── alembic.ini
├── .env.example
├── app/
│   ├── main.py                # FastAPI app, middleware, lifespan
│   ├── config.py              # Pydantic Settings — single source of truth
│   ├── api/
│   │   ├── router.py
│   │   ├── routes/health.py
│   │   └── middleware/error_handler.py
│   └── db/
│       └── session.py         # async engine + session factory
├── migrations/                # Alembic
└── tests/
    ├── conftest.py
    └── test_health.py
```

As phases land, this tree grows according to `03_BACKEND_ARCHITECTURE_DOCUMENT.md` Section B.5.

## Local development

```bash
# From repo root (recommended):
docker compose up --build backend postgres

# Or standalone:
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Health check:

```bash
curl http://localhost:8000/health
# {"status":"ok","service":"enma-backend","version":"0.1.0","env":"development"}
```

## Quality gates

```bash
ruff check .                # lint
ruff format --check .       # formatting
mypy app                    # strict typing
pytest                      # tests + 80% coverage floor
```

All four must pass for CI to be green.

## Ground rules

1. **Decimal for money.** `from decimal import Decimal` only — `float` is banned for monetary values.
2. **HTML for Telegram.** Markdown output is banned anywhere.
3. **`BaseQuery` for database.** Direct `session.execute` outside `app/db/queries/` is banned.
4. **No raw SQL in app code.** Migrations only.

See the root-level architecture documents for canonical specifications.
