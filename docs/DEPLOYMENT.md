# Deployment Guide

Production deployment of Enma Labs to AWS Fargate with managed Postgres
(Supabase). The local stack (`docker-compose.yml`) is for dev; the
production-like stack (`docker-compose.prod.yml`) is for one final sanity
check before shipping.

## Surface area

| Artefact | Path | Notes |
|---|---|---|
| Backend image | `enma-backend/Dockerfile` (`target: prod`) | Multi-stage, non-root (uid 10001), HEALTHCHECK on `/health`. |
| Gateway image | `enma-gateway/Dockerfile` (`target: prod`) | Same shape, gateway port 3000. |
| Prod compose | `docker-compose.prod.yml` | Local validation only — uses Timescale image, not Supabase. |
| Env template | `.env.production.example` | Copy, fill, **never commit**. Secret store preferred. |
| Migration | `enma-backend/migrations/versions/004_phase8_metrics_hypertables.py` | Adds metric hypertables. |

## Build & push

```sh
# Backend
docker buildx build \
  --target prod \
  --tag <registry>/enma-backend:$(git rev-parse --short HEAD) \
  --platform linux/amd64 \
  --push enma-backend

# Gateway
docker buildx build \
  --target prod \
  --tag <registry>/enma-gateway:$(git rev-parse --short HEAD) \
  --platform linux/amd64 \
  --push enma-gateway
```

## Required environment

See `.env.production.example` — every variable there is required for boot.
The Pydantic config (`enma-backend/app/config.py`) fails fast on missing
values, which is the desired behaviour. Secrets to wire from AWS Secrets
Manager (or your chosen store):

- `BACKEND_API_KEY`, `GATEWAY_HMAC_SECRET` — inter-service auth (rotate quarterly)
- `TELEGRAM_BOT_TOKEN` — from BotFather, per-firm if multi-tenant
- `LLM_API_KEY` — OpenAI-compatible bearer
- `SENTRY_DSN` — project-scoped DSN
- `DATABASE_URL` — Supabase connection string with `sslmode=require`
- `SERPER_API_KEY`, `PAGEONE_API_KEY` — optional integrations

## Pre-deploy gate

Before each release, run the Phase 8 verification block locally:

```sh
# 1. Security audit (must exit 0)
python scripts/security_audit.py

# 2. Backend tests (≥80% coverage)
cd enma-backend && pytest

# 3. Gateway tests
cd ../enma-gateway && npm test

# 4. Production-like local boot
cd .. && docker compose -f docker-compose.prod.yml --env-file .env.production up --build
```

## Post-deploy smoke

```sh
# Liveness
curl -fsS https://backend.enma.in/health
curl -fsS https://gateway.enma.in/health

# Readiness — actually pings Postgres
curl -fsS https://backend.enma.in/ready

# Logs (CloudWatch)
aws logs tail /aws/ecs/enma-backend --follow
```

## SSL / TLS

* **Database**: `sslmode=require` in `DATABASE_URL`. The
  `security_audit.py` `#17` check enforces this string is present in the
  production env template.
* **Inbound traffic**: terminate TLS at the load balancer (ALB / Cloud
  Run / Fly). Both services bind to plain HTTP internally.

## Rollback

```sh
# Each task definition revision is a rollback target. Roll back the
# backend service:
aws ecs update-service \
  --cluster enma \
  --service enma-backend \
  --task-definition enma-backend:<previous-revision> \
  --force-new-deployment
```

Roll back the migration ONLY if the new revision is provably the cause —
`alembic downgrade <rev>` runs `004_phase8_metrics_hypertables.py::downgrade`
which is idempotent.
