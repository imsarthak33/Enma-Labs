"""013 — P1: brain_events typed-event substrate.

The first slice of Layer A's Brain Ingestion (P1). Every ingestion
source — Tally (A2), then Gmail / WhatsApp groups / GSTN portal (A3) —
writes normalised events into this one table. The doc's standing note
makes this explicit: "L2 episodic memory ... is subsumed by the Brain
layer, becomes brain_events in P1." So this table is the canonical
landing zone; the per-source readers in later units only translate
their domain into ``brain_events`` rows.

Shape
-----
``brain_events`` — append-only typed event log, one row per fact
    observed in a source system. Mirrors ``agentic_trajectories``
    (migration 012): TimescaleDB hypertable on the time column so
    high-cardinality multi-firm growth degrades into chunks, with a
    composite PK ``(occurred_at, id)`` because hypertables require the
    partition column in every unique index. ``occurred_at`` is when the
    event happened in the source (e.g. the Tally voucher date, the
    email's Date header); ``ingested_at`` is when Enma recorded it.

    ``source`` is CHECK-constrained (not a Postgres ENUM — the set
    grows as we add ingestion adapters). ``event_type`` is free text so
    each adapter names its own events without a migration per type.
    ``payload`` is the normalised JSONB body.

Idempotent ingestion
--------------------
Sources re-poll (a Gmail sync, a Tally re-read) and will re-present the
same fact. ``dedup_key`` is the source-stable identifier (Gmail
Message-ID, Tally voucher GUID, GSTN ARN, …). A partial unique index on
``(ca_firm_id, source, dedup_key, occurred_at)`` makes a re-ingest a
no-op via ``ON CONFLICT DO NOTHING`` in :class:`BrainEventQuery`.
``occurred_at`` is in the unique tuple because Timescale requires the
partition column in every unique index — harmless here since a given
source event has a fixed ``occurred_at``. Rows with a NULL
``dedup_key`` (internal events that carry no natural key) are never
deduped.

Idempotency (migration-level)
-----------------------------
All DDL is ``IF NOT EXISTS`` / ``DO $$ EXCEPTION WHEN duplicate_object``
guarded. The hypertable conversion is gated on ``pg_extension`` exactly
like migration 012, so the Timescale-less prod database (and local dev
without the extension) upgrades cleanly to a regular table.

Revision ID: 013
Revises: 012
Create Date: 2026-06-23
"""

from __future__ import annotations

from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # brain_events — typed event log, one row per observed source fact
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS brain_events (
            id UUID NOT NULL DEFAULT uuid_generate_v4(),
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ca_firm_id UUID NOT NULL
                REFERENCES ca_firms(id) ON DELETE CASCADE,
            client_id UUID
                REFERENCES clients(id) ON DELETE SET NULL,
            source VARCHAR(40) NOT NULL,
            event_type VARCHAR(60) NOT NULL,
            dedup_key TEXT,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (occurred_at, id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_brain_events_firm_time
            ON brain_events (ca_firm_id, occurred_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_brain_events_firm_client_time
            ON brain_events (ca_firm_id, client_id, occurred_at DESC)
            WHERE client_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_brain_events_firm_source_type
            ON brain_events (ca_firm_id, source, event_type, occurred_at DESC)
        """
    )
    # Partial unique index — idempotent re-ingest. Includes occurred_at so
    # the index is valid on a Timescale hypertable (partition col rule).
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_brain_events_dedup
            ON brain_events (ca_firm_id, source, dedup_key, occurred_at)
            WHERE dedup_key IS NOT NULL
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE brain_events
                ADD CONSTRAINT ck_brain_events_source
                CHECK (source IN (
                    'tally',
                    'gmail',
                    'whatsapp_group',
                    'gstn_portal',
                    'enma_internal'
                ));
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )

    # Hypertable conversion — gated on Timescale presence, matches 012.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
            PERFORM create_hypertable(
              'brain_events', 'occurred_at',
              chunk_time_interval => INTERVAL '7 days',
              if_not_exists => TRUE
            );
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_brain_events_dedup")
    op.execute("DROP INDEX IF EXISTS ix_brain_events_firm_source_type")
    op.execute("DROP INDEX IF EXISTS ix_brain_events_firm_client_time")
    op.execute("DROP INDEX IF EXISTS ix_brain_events_firm_time")
    op.execute("DROP TABLE IF EXISTS brain_events")
