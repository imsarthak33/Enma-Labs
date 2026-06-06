# Operational Monitoring

Schema + SQL views that power the Phase 8 dashboard.

## What lives where

| File | Purpose |
|---|---|
| `../enma-backend/migrations/versions/004_phase8_metrics_hypertables.py` | Creates `processing_metrics`, `token_usage` (hypertables) and `error_log`. |
| `dashboard_views.sql` | Continuous aggregates + tile views for the dashboard. |
| `grafana_dashboard.json` | Drop-in Grafana dashboard wired to the views above. |

## Install

```sh
# Migrate the schema (idempotent — gated on the TimescaleDB extension).
cd enma-backend && alembic upgrade head

# Create / refresh the dashboard views and continuous-aggregate policies.
psql "$DATABASE_URL" -f ../monitoring/dashboard_views.sql
```

Compression and retention policies live in the migration:

| Hypertable | Compress after | Retain for |
|---|---|---|
| `processing_metrics` | 7 days | 90 days |
| `token_usage`        | 7 days | 90 days |

## Tile inventory

1. **Processing latency per stage** — `processing_latency_hourly` (continuous
   aggregate), `processing_latency_last_24h` (instant tile)
2. **Token usage per model / day** — `token_usage_daily`,
   `token_spend_last_24h`
3. **Error rate per endpoint** — `error_rate_last_1h`, `error_rate_last_24h`
4. **Active firms / clients** — `active_firms_today`, `upload_volume_last_24h`
5. **Idempotency log volume** — `idempotency_log_size` (should plateau once
   the daily cleanup cron is running steady-state)

## Wiring producers

`processing_metrics` and `token_usage` are written by:

- `app/services/llm.py` — token usage on every LLM call (already in place;
  the migration formalises the table).
- `app/agents/pipeline.py` — per-stage latency.
- `app/identity/resolver.py` — `identity` stage latency.

`error_log` is written by the FastAPI exception handler in
`app/api/middleware/error_handler.py`; it samples one row per request that
ends in a 5xx, with the `request_id` so Sentry can be cross-pivoted.

If you add a new pipeline stage, add the matching writer call — don't write
straight to the table from outside a query class; use
`app/db/queries/metrics.py` (planned).

## Importing into Grafana

1. Add the production Postgres as a Grafana data source (read-only role).
2. Dashboards → New → Import → upload `grafana_dashboard.json`.
3. Bind the data source when prompted.
