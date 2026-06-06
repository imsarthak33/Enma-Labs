"""003 — Phase 6: pending_assignments table.

Identity resolution stage 5 ("explicit ask") queues a routing decision
here while it waits for the CA to pick a client via inline-button
callback. The row is short-lived — a row older than
``PENDING_ASSIGNMENT_TTL_SECONDS`` is treated as expired by the
resolver and ignored.

The table is tenant-scoped (``ca_firm_id``) with full RLS so a stale
callback from another firm's chat can never resolve cross-tenant.

Revision ID: 003
Revises: 002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_assignments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("ca_firm_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "message_id",
            sa.BigInteger(),
            nullable=True,
            comment="Original document message that triggered the prompt.",
        ),
        sa.Column(
            "file_ids",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="Telegram file_ids to attach once the CA picks a client.",
        ),
        sa.Column(
            "candidate_client_ids",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="Pre-narrowed client UUIDs offered to the CA (may be empty).",
        ),
        sa.Column(
            "prompt_message_id",
            sa.BigInteger(),
            nullable=True,
            comment="message_id of the 'which client?' prompt we sent.",
        ),
        sa.Column(
            "resolved_client_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="After this time the resolver treats the row as abandoned.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["ca_firm_id"], ["ca_firms.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["resolved_client_id"], ["clients.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(file_ids) = 'array'",
            name="ck_pending_assignments_file_ids_is_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(candidate_client_ids) = 'array'",
            name="ck_pending_assignments_candidates_is_array",
        ),
    )

    op.create_index(
        "idx_pending_assignments_open",
        "pending_assignments",
        ["ca_firm_id", "chat_id", "expires_at"],
        postgresql_where=sa.text("resolved_at IS NULL"),
    )

    # ----- RLS -------------------------------------------------------------
    op.execute("ALTER TABLE pending_assignments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE pending_assignments FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_pending_assignments ON pending_assignments
            FOR ALL
            USING (ca_firm_id = current_setting('app.current_firm_id', true)::uuid)
            WITH CHECK (ca_firm_id = current_setting('app.current_firm_id', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_pending_assignments ON pending_assignments"
    )
    op.execute("ALTER TABLE pending_assignments DISABLE ROW LEVEL SECURITY")
    op.drop_index("idx_pending_assignments_open", table_name="pending_assignments")
    op.drop_table("pending_assignments")
