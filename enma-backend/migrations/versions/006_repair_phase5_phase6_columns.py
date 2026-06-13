"""006 — Idempotent repair: re-assert Phase 5 / Phase 6 columns in production.

Sentry surfaced three ``UndefinedColumnError`` crashes from the live
backend that map directly to columns introduced in migrations 002 and
003:

  * ``ca_firm_rules.directive``                — added in 002 (Phase 5)
  * ``clients.gst_tds_deductor``               — added in 002 (Phase 5)
  * ``pending_assignments.expires_at`` (and the table itself) — added in
    003 (Phase 6)

The production database was last upgraded to a revision *prior* to 002
(or had migration state desynced from the script directory by a manual
intervention). Rolling forward by re-running 002 / 003 is unsafe because
their DDL is not ``IF NOT EXISTS``-guarded and will fail on a partially
applied schema.

This migration encodes the *minimum repair* needed to make production
reflect the SQLAlchemy model definitions, using idempotent SQL so the
same script is a no-op on any environment that already ran 002 and 003
cleanly (local, CI, staging).

ALL DDL here is guarded:
  * ``ADD COLUMN IF NOT EXISTS``
  * ``CREATE TABLE IF NOT EXISTS``
  * ``CREATE INDEX IF NOT EXISTS``
  * ``DO $$ BEGIN ... EXCEPTION WHEN duplicate_object ... END $$`` for
    constraints and policies (no ``IF NOT EXISTS`` in vanilla PG for those).

Revision ID: 006
Revises: 005
Create Date: 2026-06-13
"""

from __future__ import annotations

from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # ca_firm_rules.directive  +  CHECK constraint
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE ca_firm_rules
            ADD COLUMN IF NOT EXISTS directive JSONB
                NOT NULL DEFAULT '{}'::jsonb
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE ca_firm_rules
                ADD CONSTRAINT ck_ca_firm_rules_directive_is_object
                CHECK (jsonb_typeof(directive) = 'object');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )

    # ------------------------------------------------------------------
    # clients.gst_tds_deductor
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE clients
            ADD COLUMN IF NOT EXISTS gst_tds_deductor BOOLEAN
                NOT NULL DEFAULT FALSE
        """
    )

    # ------------------------------------------------------------------
    # pending_assignments — entire table + RLS may be missing in prod
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_assignments (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            ca_firm_id UUID NOT NULL REFERENCES ca_firms(id) ON DELETE CASCADE,
            chat_id BIGINT NOT NULL,
            message_id BIGINT,
            file_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            candidate_client_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            prompt_message_id BIGINT,
            resolved_client_id UUID REFERENCES clients(id) ON DELETE SET NULL,
            resolved_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_pending_assignments_file_ids_is_array
                CHECK (jsonb_typeof(file_ids) = 'array'),
            CONSTRAINT ck_pending_assignments_candidates_is_array
                CHECK (jsonb_typeof(candidate_client_ids) = 'array')
        )
        """
    )
    # If the table existed but ``expires_at`` was dropped manually, restore it.
    op.execute(
        """
        ALTER TABLE pending_assignments
            ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ NOT NULL
                DEFAULT (NOW() + INTERVAL '1 hour')
        """
    )
    # Drop the temporary default we used purely to satisfy NOT NULL on
    # back-fill — the model itself supplies expires_at on every insert.
    op.execute("ALTER TABLE pending_assignments ALTER COLUMN expires_at DROP DEFAULT")

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_pending_assignments_open
            ON pending_assignments (ca_firm_id, chat_id, expires_at)
            WHERE resolved_at IS NULL
        """
    )

    # RLS — enable + force + policy. ALTER TABLE … ENABLE RLS is idempotent.
    op.execute("ALTER TABLE pending_assignments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE pending_assignments FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
            CREATE POLICY tenant_isolation_pending_assignments ON pending_assignments
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
    # 006 is a forward-only repair. It is intentionally a no-op on downgrade
    # so we do not destroy data on operators who reach this revision and
    # then ``alembic downgrade -1``. The columns and table remain owned by
    # migrations 002 and 003 — downgrade those instead.
    pass
