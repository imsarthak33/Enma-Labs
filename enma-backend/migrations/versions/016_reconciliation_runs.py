"""016 — reconciliation_runs: immutable audit of Tri-Way ITC recon (ADR-015).

One row per reconciliation a CA runs for a (client, period). Mirrors the
``tally_export_runs`` audit pattern (W3): immutable, firm + client scoped,
records the per-bucket counts and the recoverable / at-risk ITC totals so
a CA can be told "this is the same recon you ran on Tuesday" and so the
TA-1 outcome meter has a durable source.

Idempotency
-----------
``CREATE TABLE IF NOT EXISTS`` + guarded CHECK. Revises 015.

Revision ID: 016
Revises: 015
Create Date: 2026-06-24
"""

from __future__ import annotations

from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS reconciliation_runs (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            ca_firm_id UUID NOT NULL
                REFERENCES ca_firms(id) ON DELETE CASCADE,
            client_id UUID NOT NULL
                REFERENCES clients(id) ON DELETE CASCADE,
            filing_period_month INTEGER NOT NULL,
            filing_period_year INTEGER NOT NULL,
            kind VARCHAR(20) NOT NULL DEFAULT 'invoice_vs_2b',
            matched_count INTEGER NOT NULL DEFAULT 0,
            amount_mismatch_count INTEGER NOT NULL DEFAULT 0,
            in_books_not_in_2b_count INTEGER NOT NULL DEFAULT 0,
            in_2b_not_in_books_count INTEGER NOT NULL DEFAULT 0,
            recoverable_itc NUMERIC(18, 2) NOT NULL DEFAULT 0,
            at_risk_itc NUMERIC(18, 2) NOT NULL DEFAULT 0,
            report_sha256 VARCHAR(64),
            run_by_chat_id BIGINT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_reconciliation_runs_firm_client_period
            ON reconciliation_runs
            (ca_firm_id, client_id, filing_period_year, filing_period_month)
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE reconciliation_runs
                ADD CONSTRAINT ck_reconciliation_runs_month
                CHECK (filing_period_month BETWEEN 1 AND 12);
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_reconciliation_runs_firm_client_period")
    op.execute("DROP TABLE IF EXISTS reconciliation_runs")
