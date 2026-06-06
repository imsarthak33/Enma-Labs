# syntax=docker/dockerfile:1.7
# =============================================================================
# enma-backend — multi-stage build.
#   * base  → pinned python + system deps (libpq for asyncpg notice loop)
#   * dev   → editable install + dev extras, code mounted via compose volume
#   * prod  → wheel-built, non-root user, gunicorn+uvicorn worker
# =============================================================================

ARG PYTHON_VERSION=3.12-slim-bookworm

# ---------- base -------------------------------------------------------------
FROM python:${PYTHON_VERSION} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---------- dev --------------------------------------------------------------
FROM base AS dev

COPY pyproject.toml ./
# Install build backend + the project (without source) so dependencies cache.
RUN pip install --upgrade pip \
    && pip install -e ".[dev]" || true

COPY . .
RUN pip install -e ".[dev]"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---------- prod -------------------------------------------------------------
FROM base AS prod

# Build wheels in an isolated layer for caching.
COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./

RUN pip install --upgrade pip \
    && pip install .

# Non-root runtime user.
RUN useradd --create-home --uid 10001 enma \
    && chown -R enma:enma /app
USER enma

EXPOSE 8000

# Probe the liveness endpoint. Falls back to exit 1 on any non-2xx.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import sys, urllib.request as u; \
sys.exit(0 if u.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)" \
    || exit 1

# Gunicorn manages workers; UvicornWorker speaks ASGI. WEB_CONCURRENCY lets
# the orchestrator (Fargate/EKS) right-size workers without rebuilding.
ENV WEB_CONCURRENCY=2
CMD ["sh", "-c", "exec gunicorn app.main:app \
     --worker-class uvicorn.workers.UvicornWorker \
     --workers ${WEB_CONCURRENCY} \
     --bind 0.0.0.0:8000 \
     --access-logfile - \
     --error-logfile - \
     --graceful-timeout 30 \
     --timeout 60"]
