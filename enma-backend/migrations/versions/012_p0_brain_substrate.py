"""012 — P0: brain substrate (trajectories + outcomes + corrections + bugs).

The substrate the rest of the Brain layer reads from. Per ADR-012, all
four tables ship in one atomic migration so beta cannot start in a
state where supervisor turns are captured but CA corrections are not —
that gap would burn the LoRA training signal P9 depends on.

What this migration adds
------------------------
``agentic_trajectories`` — append-only event log, one row per supervisor
    turn. TimescaleDB hypertable on ``created_at`` so high-cardinality
    multi-firm growth degrades into chunks. Composite PK ``(created_at,
    id)`` because hypertables require the time column in every unique
    index. Retention policy is deferred to P1.

``outcome_units`` — every billable unit Enma generates. Flexible
    ``kind`` enum (CHECK constraint, not a Postgres ENUM type — the
    set will grow) + ``quantity NUMERIC(18,4)`` + ``metadata JSONB``
    for kind-specific detail. The TA-1 outcome meter is a
    ``SUM(quantity) WHERE kind = ?`` against this table.

``verdict_corrections`` — the LoRA corpus. Every CA correction of a
    tax verdict lands here with anonymized invoice features.
    Anonymization is done before insert (single-stage write); the
    table never holds un-anonymized rows. FK to ``documents`` is
    ``ON DELETE SET NULL`` so the corpus survives a document delete.

``bug_reports`` — captured by ``/bug "<text>"``. Plain text body,
    severity defaults to ``'unspecified'`` (the slash command can
    upgrade it).

Confidence
----------
No DDL — ``confidence`` lives inside the existing ``documents.tax_verdict``
JSONB. The pipeline always writes a string-serialised Decimal ``"1.0"``
when the engine has no better number. JSONB doesn't need a column add.

Idempotency
-----------
All DDL is ``IF NOT EXISTS`` / ``DO $$ EXCEPTION WHEN duplicate_object``
guarded. Hypertable conversion is gated on ``pg_extension``
matching migration 004's pattern so non-Timescale local dev still
upgrades cleanly.

Revision ID: 012
Revises: 011
Create Date: 2026-06-20
"""

from __future__ import annotations

from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # agentic_trajectories — append-only supervisor-turn log
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agentic_trajectories (
            id UUID NOT NULL DEFAULT uuid_generate_v4(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ca_firm_id UUID NOT NULL
                REFERENCES ca_firms(id) ON DELETE CASCADE,
            chat_id BIGINT,
            user_text TEXT NOT NULL,
            tool_calls_made JSONB NOT NULL DEFAULT '[]'::jsonb,
            final_reply TEXT,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            llm_calls JSONB NOT NULL DEFAULT '[]'::jsonb,
            PRIMARY KEY (created_at, id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_agentic_trajectories_firm_time
            ON agentic_trajectories (ca_firm_id, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_agentic_trajectories_chat_time
            ON agentic_trajectories (chat_id, created_at DESC)
            WHERE chat_id IS NOT NULL
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE agentic_trajectories
                ADD CONSTRAINT ck_agentic_trajectories_tokens_nonneg
                CHECK (input_tokens >= 0 AND output_tokens >= 0);
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )

    # Hypertable conversion — gated on Timescale presence, matches 004.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
            PERFORM create_hypertable(
              'agentic_trajectories', 'created_at',
              chunk_time_interval => INTERVAL '1 day',
              if_not_exists => TRUE
            );
          END IF;
        END
        $$;
        """
    )

    # ------------------------------------------------------------------
    # outcome_units — billable units per firm/client/kind
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS outcome_units (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            ca_firm_id UUID NOT NULL
                REFERENCES ca_firms(id) ON DELETE CASCADE,
            client_id UUID
                REFERENCES clients(id) ON DELETE SET NULL,
            kind VARCHAR(40) NOT NULL,
            quantity NUMERIC(18, 4) NOT NULL,
            confidence NUMERIC(5, 4),
            ca_approval_status VARCHAR(20) NOT NULL DEFAULT 'pending',
            related_document_id UUID
                REFERENCES documents(id) ON DELETE SET NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_outcome_units_firm_kind_time
            ON outcome_units (ca_firm_id, kind, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_outcome_units_firm_client_time
            ON outcome_units (ca_firm_id, client_id, created_at DESC)
            WHERE client_id IS NOT NULL
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE outcome_units
                ADD CONSTRAINT ck_outcome_units_kind
                CHECK (kind IN (
                    'itc_recovered_inr',
                    'filed_return',
                    'drafted_notice',
                    'reconciled_period',
                    'invoice_processed',
                    'liaison_message_sent',
                    'tally_export_generated',
                    'client_ledger_exported'
                ));
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE outcome_units
                ADD CONSTRAINT ck_outcome_units_approval
                CHECK (ca_approval_status IN (
                    'pending', 'approved', 'rejected', 'auto_approved'
                ));
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE outcome_units
                ADD CONSTRAINT ck_outcome_units_quantity_nonneg
                CHECK (quantity >= 0);
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE outcome_units
                ADD CONSTRAINT ck_outcome_units_confidence_range
                CHECK (confidence IS NULL
                       OR (confidence >= 0 AND confidence <= 1));
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )

    # ------------------------------------------------------------------
    # verdict_corrections — LoRA training corpus
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS verdict_corrections (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            ca_firm_id UUID NOT NULL
                REFERENCES ca_firms(id) ON DELETE CASCADE,
            source_document_id UUID
                REFERENCES documents(id) ON DELETE SET NULL,
            original_verdict JSONB NOT NULL,
            corrected_verdict JSONB NOT NULL,
            correction_reason_text TEXT,
            corrected_by_chat_id BIGINT,
            track CHAR(1) NOT NULL DEFAULT 'A',
            confidence_self_assessed NUMERIC(5, 4),
            invoice_features_anonymized JSONB NOT NULL DEFAULT '{}'::jsonb,
            anonymized_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_verdict_corrections_firm_time
            ON verdict_corrections (ca_firm_id, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_verdict_corrections_document
            ON verdict_corrections (source_document_id)
            WHERE source_document_id IS NOT NULL
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE verdict_corrections
                ADD CONSTRAINT ck_verdict_corrections_track
                CHECK (track IN ('A', 'B'));
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE verdict_corrections
                ADD CONSTRAINT ck_verdict_corrections_confidence_range
                CHECK (confidence_self_assessed IS NULL
                       OR (confidence_self_assessed >= 0
                           AND confidence_self_assessed <= 1));
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )

    # ------------------------------------------------------------------
    # bug_reports — captured via /bug
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bug_reports (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            ca_firm_id UUID NOT NULL
                REFERENCES ca_firms(id) ON DELETE CASCADE,
            chat_id BIGINT,
            severity VARCHAR(20) NOT NULL DEFAULT 'unspecified',
            body TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_bug_reports_firm_time
            ON bug_reports (ca_firm_id, created_at DESC)
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE bug_reports
                ADD CONSTRAINT ck_bug_reports_severity
                CHECK (severity IN (
                    'unspecified', 'low', 'medium', 'high', 'critical'
                ));
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )


def downgrade() -> None:
    # bug_reports
    op.execute("DROP INDEX IF EXISTS ix_bug_reports_firm_time")
    op.execute("DROP TABLE IF EXISTS bug_reports")

    # verdict_corrections
    op.execute("DROP INDEX IF EXISTS ix_verdict_corrections_document")
    op.execute("DROP INDEX IF EXISTS ix_verdict_corrections_firm_time")
    op.execute("DROP TABLE IF EXISTS verdict_corrections")

    # outcome_units
    op.execute("DROP INDEX IF EXISTS ix_outcome_units_firm_client_time")
    op.execute("DROP INDEX IF EXISTS ix_outcome_units_firm_kind_time")
    op.execute("DROP TABLE IF EXISTS outcome_units")

    # agentic_trajectories — hypertable drop is just DROP TABLE under
    # Timescale; no special teardown needed for retention/compression
    # policies because P0 didn't install any.
    op.execute("DROP INDEX IF EXISTS ix_agentic_trajectories_chat_time")
    op.execute("DROP INDEX IF EXISTS ix_agentic_trajectories_firm_time")
    op.execute("DROP TABLE IF EXISTS agentic_trajectories")
