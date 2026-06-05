-- =====================================================================
-- Enma — PostgreSQL extension bootstrap
-- Runs once on first container start (timescaledb image executes any
-- *.sql in /docker-entrypoint-initdb.d/ during cluster initialization).
--
-- These extensions are prerequisites for every later migration:
--   * uuid-ossp   — uuid_generate_v4() default for all PKs
--   * vector      — 1024-dim embeddings for ca_firm_rules (pgvector)
--   * pg_trgm     — fuzzy text matching for client name resolution
--   * timescaledb — hypertables for metrics / token usage
--
-- The timescaledb extension is pre-installed in the Docker image; CREATE
-- EXTENSION still has to be issued per-database.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Sanity probe so failures surface early in container logs.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        RAISE EXCEPTION 'pgvector extension failed to install';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
        RAISE EXCEPTION 'pg_trgm extension failed to install';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'uuid-ossp') THEN
        RAISE EXCEPTION 'uuid-ossp extension failed to install';
    END IF;
END
$$;
