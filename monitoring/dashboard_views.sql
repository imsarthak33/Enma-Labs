-- =============================================================================
-- Enma Labs — operational dashboard views
--
-- Run once against the production database (idempotent — uses
-- CREATE OR REPLACE / IF NOT EXISTS everywhere). The views compose the
-- queries Grafana / Metabase consume; the materialised aggregates power
-- the low-latency tiles.
--
-- Numbering matches the Phase 8 monitoring dashboard layout (doc 06 §5):
--   1. Processing latency per pipeline stage
--   2. Token usage per model per day
--   3. Error rate per endpoint
--   4. Active firms & clients
--   5. Idempotency-log volume (operational health)
--
-- Conventions
--   * All views aggregate on (recorded_at | occurred_at) buckets so the
--     same query works against the hypertable and the compressed chunks.
--   * No view filters by ca_firm_id — RLS handles tenant scoping at the
--     connection layer; dashboards run as the platform-owner role.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1) Processing latency per pipeline stage (continuous aggregate, hourly bucket)
-- -----------------------------------------------------------------------------
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
    EXECUTE $sql$
      CREATE MATERIALIZED VIEW IF NOT EXISTS processing_latency_hourly
      WITH (timescaledb.continuous) AS
      SELECT
        time_bucket('1 hour', recorded_at) AS bucket,
        stage,
        COUNT(*)                                                AS samples,
        percentile_cont(0.50) WITHIN GROUP (ORDER BY ms)        AS p50_ms,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY ms)        AS p95_ms,
        percentile_cont(0.99) WITHIN GROUP (ORDER BY ms)        AS p99_ms,
        SUM(CASE WHEN success THEN 0 ELSE 1 END)                AS failures
      FROM processing_metrics
      GROUP BY bucket, stage
      WITH NO DATA;
    $sql$;
    PERFORM add_continuous_aggregate_policy(
      'processing_latency_hourly',
      start_offset => INTERVAL '7 days',
      end_offset   => INTERVAL '1 hour',
      schedule_interval => INTERVAL '15 minutes',
      if_not_exists => TRUE
    );
  END IF;
END
$$;

-- Latest-stage snapshot (used for dashboard tiles, no time bucket).
CREATE OR REPLACE VIEW processing_latency_last_24h AS
SELECT
  stage,
  COUNT(*)                                                AS samples,
  percentile_cont(0.50) WITHIN GROUP (ORDER BY ms)        AS p50_ms,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY ms)        AS p95_ms,
  percentile_cont(0.99) WITHIN GROUP (ORDER BY ms)        AS p99_ms,
  SUM(CASE WHEN NOT success THEN 1 ELSE 0 END)            AS failures
FROM processing_metrics
WHERE recorded_at > NOW() - INTERVAL '24 hours'
GROUP BY stage
ORDER BY stage;

-- -----------------------------------------------------------------------------
-- 2) Token usage per model per day
-- -----------------------------------------------------------------------------
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
    EXECUTE $sql$
      CREATE MATERIALIZED VIEW IF NOT EXISTS token_usage_daily
      WITH (timescaledb.continuous) AS
      SELECT
        time_bucket('1 day', recorded_at) AS day,
        model,
        request_type,
        COUNT(*)                              AS calls,
        SUM(input_tokens)::BIGINT             AS input_tokens,
        SUM(output_tokens)::BIGINT            AS output_tokens,
        SUM(cost_usd_micro)::BIGINT           AS cost_usd_micro
      FROM token_usage
      GROUP BY day, model, request_type
      WITH NO DATA;
    $sql$;
    PERFORM add_continuous_aggregate_policy(
      'token_usage_daily',
      start_offset => INTERVAL '30 days',
      end_offset   => INTERVAL '1 hour',
      schedule_interval => INTERVAL '30 minutes',
      if_not_exists => TRUE
    );
  END IF;
END
$$;

-- Spend-rate tile — last 24h in USD.
CREATE OR REPLACE VIEW token_spend_last_24h AS
SELECT
  model,
  SUM(cost_usd_micro) / 1000000.0 AS cost_usd,
  SUM(input_tokens)              AS input_tokens,
  SUM(output_tokens)             AS output_tokens
FROM token_usage
WHERE recorded_at > NOW() - INTERVAL '24 hours'
GROUP BY model
ORDER BY cost_usd DESC;

-- -----------------------------------------------------------------------------
-- 3) Error rate per endpoint (last 1h / 24h)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW error_rate_last_1h AS
SELECT
  endpoint,
  COUNT(*) AS error_count,
  COUNT(DISTINCT ca_firm_id) AS firms_impacted
FROM error_log
WHERE occurred_at > NOW() - INTERVAL '1 hour'
GROUP BY endpoint
ORDER BY error_count DESC;

CREATE OR REPLACE VIEW error_rate_last_24h AS
SELECT
  endpoint,
  COUNT(*) AS error_count,
  COUNT(DISTINCT ca_firm_id) AS firms_impacted,
  COUNT(*) FILTER (WHERE severity = 'critical') AS critical_count
FROM error_log
WHERE occurred_at > NOW() - INTERVAL '24 hours'
GROUP BY endpoint
ORDER BY error_count DESC;

-- -----------------------------------------------------------------------------
-- 4) Active firms & clients
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW active_firms_today AS
SELECT
  COUNT(DISTINCT f.id)                                                  AS firm_count,
  COUNT(DISTINCT c.id) FILTER (WHERE c.is_active)                       AS active_client_count,
  COUNT(DISTINCT d.ca_firm_id) FILTER (WHERE d.created_at > NOW() - INTERVAL '24 hours')
                                                                        AS firms_with_recent_uploads
FROM ca_firms f
LEFT JOIN clients   c ON c.ca_firm_id = f.id
LEFT JOIN documents d ON d.ca_firm_id = f.id;

CREATE OR REPLACE VIEW upload_volume_last_24h AS
SELECT
  date_trunc('hour', created_at) AS hour,
  COUNT(*)                       AS document_count,
  COUNT(DISTINCT ca_firm_id)     AS firms,
  COUNT(DISTINCT client_id)      AS clients
FROM documents
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY hour
ORDER BY hour;

-- -----------------------------------------------------------------------------
-- 5) Idempotency log volume (operational health — should plateau at ~72h)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW idempotency_log_size AS
SELECT
  COUNT(*)                                       AS total_rows,
  COUNT(*) FILTER (WHERE processed_at > NOW() - INTERVAL '1 hour')   AS rows_last_1h,
  COUNT(*) FILTER (WHERE processed_at > NOW() - INTERVAL '24 hours') AS rows_last_24h,
  MIN(processed_at)                              AS oldest_row_at,
  MAX(processed_at)                              AS newest_row_at
FROM idempotency_log;
