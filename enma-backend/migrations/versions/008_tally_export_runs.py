"""008 — W3: Tally Prime export audit table.

Phase 6 (W3) introduces the ``export_to_tally`` supervisor tool, which
composes a Tally Prime ENVELOPE XML for a (client, month, year) tuple
and delivers it to the CA as a Telegram document. Each successful
export writes one immutable audit row to ``tally_export_runs`` so a
CA can later answer "when did I last export this period and what was
the voucher count" without re-running the export.

This migration is idempotent (``CREATE TABLE IF NOT EXISTS`` +
``CREATE INDEX IF NOT EXISTS`` + duplicate-object-tolerant policy
creation), matching the pattern established by 006. RLS is enabled
on the table for defense in depth — application-layer scoping via
``BaseQuery`` is the primary control, but the policy ensures that any
direct ``session.execute`` accidentally introduced in the future
cannot leak rows across firms.

Revision ID: 008
Revises: 007
Create Date: 2026-06-15
"""

from __future__ import annotations

from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tally_export_runs (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            ca_firm_id UUID NOT NULL REFERENCES ca_firms(id) ON DELETE CASCADE,
            client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            filing_month INTEGER NOT NULL,
            filing_year INTEGER NOT NULL,
            voucher_count INTEGER NOT NULL,
            file_sha256 TEXT NOT NULL,
            exported_by_chat_id BIGINT NOT NULL,
            exported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tally_export_runs_firm_client_period
            ON tally_export_runs (ca_firm_id, client_id, filing_year, filing_month)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tally_export_runs_recent
            ON tally_export_runs (ca_firm_id, exported_at DESC)
        """
    )

    # RLS — defense in depth. Application-layer ``BaseQuery`` is the
    # primary scoping mechanism; this policy ensures any accidental
    # raw session.execute on this table cannot cross firm boundaries.
    op.execute("ALTER TABLE tally_export_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tally_export_runs FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
            CREATE POLICY tenant_isolation_tally_export_runs ON tally_export_runs
                FOR ALL
                USING (ca_firm_id = current_setting('app.current_firm_id', true)::uuid)
                WITH CHECK (ca_firm_id = current_setting('app.current_firm_id', true)::uuid);
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tally_export_runs CASCADE")
