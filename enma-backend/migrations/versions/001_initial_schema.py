"""001 — Initial schema: all core tables, indexes, RLS policies, hypertables.

Phase 1 deliverable. Creates:
  - ca_firms, firm_users
  - clients
  - documents
  - ca_firm_rules (with VECTOR(1024) column)
  - conversations
  - tasks
  - filing_approvals
  - idempotency_log
  - identity_resolution_log
  - client_notifications
  - client_notifications

Plus:
  - All indexes (btree, partial, IVFFlat vector)
  - Row-Level Security policies on tenant-scoped tables
  - TimescaleDB hypertable conversions

Revision ID: 001
Revises: —
Create Date: 2026-06-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ==================================================================
    # EXTENSIONS (idempotent — already created by init-extensions.sql
    # but safe to re-run in case migration runs on a fresh DB)
    # ==================================================================
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "vector"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')

    # ==================================================================
    # TABLE: ca_firms (top-level tenant — no ca_firm_id column)
    # ==================================================================
    op.create_table(
        "ca_firms",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("firm_name", sa.Text(), nullable=False),
        sa.Column("admin_chat_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "telegram_bot_token",
            sa.Text(),
            nullable=False,
            comment="Encrypted at rest — never log or expose.",
        ),
        sa.Column(
            "subscription_tier", sa.String(50), server_default=sa.text("'starter'"), nullable=False
        ),
        sa.Column("max_clients", sa.Integer(), server_default=sa.text("50"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ==================================================================
    # TABLE: firm_users
    # ==================================================================
    op.create_table(
        "firm_users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("ca_firm_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column(
            "role",
            sa.String(20),
            server_default=sa.text("'member'"),
            nullable=False,
            comment="admin | partner | member",
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["ca_firm_id"], ["ca_firms.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("ca_firm_id", "chat_id", name="uq_firm_users_firm_chat"),
    )

    # ==================================================================
    # TABLE: clients
    # ==================================================================
    op.create_table(
        "clients",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("ca_firm_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trade_name", sa.Text(), nullable=False),
        sa.Column("legal_name", sa.Text(), nullable=True),
        sa.Column(
            "gstin",
            sa.String(15),
            nullable=True,
            comment="15-char GSTIN. Validated at app layer via regex + checksum.",
        ),
        sa.Column("pan", sa.String(10), nullable=True, comment="10-char PAN. Encrypted at rest."),
        sa.Column("state_code", sa.String(2), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("contact_email", sa.Text(), nullable=True),
        sa.Column("contact_phone", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["ca_firm_id"], ["ca_firms.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("ca_firm_id", "gstin", name="uq_clients_firm_gstin"),
    )

    # ==================================================================
    # TABLE: documents
    # ==================================================================
    op.create_table(
        "documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("ca_firm_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "document_type",
            sa.String(50),
            nullable=False,
            comment="B2B_INVOICE | B2C_INVOICE | FREIGHT | RESTAURANT | IMPORT | PROFESSIONAL | CAPITAL_GOODS",
        ),
        sa.Column(
            "source_file_ids", postgresql.JSONB(), nullable=False, comment="Telegram file_id array."
        ),
        sa.Column(
            "extraction_data",
            postgresql.JSONB(),
            nullable=False,
            comment="Full structured extraction output.",
        ),
        sa.Column(
            "tax_verdict",
            postgresql.JSONB(),
            nullable=True,
            comment="Five optimisation variables: claim, defer, block, rcm, tds.",
        ),
        sa.Column(
            "verification_result",
            postgresql.JSONB(),
            nullable=True,
            comment="Red-team deterministic verification output.",
        ),
        sa.Column("filing_period_month", sa.Integer(), nullable=True),
        sa.Column("filing_period_year", sa.Integer(), nullable=True),
        sa.Column(
            "processing_status",
            sa.String(20),
            server_default=sa.text("'pending'"),
            nullable=False,
            comment="pending | processing | completed | failed",
        ),
        sa.Column("processing_time_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["ca_firm_id"], ["ca_firms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_documents_firm_client", "documents", ["ca_firm_id", "client_id"])
    op.create_index(
        "idx_documents_filing",
        "documents",
        ["ca_firm_id", "filing_period_year", "filing_period_month"],
    )

    # ==================================================================
    # TABLE: ca_firm_rules (with pgvector VECTOR(1024) column)
    # ==================================================================
    op.create_table(
        "ca_firm_rules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("ca_firm_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "client_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="NULL = firm-wide rule; set = client-specific.",
        ),
        sa.Column(
            "rule_type",
            sa.String(50),
            nullable=False,
            comment="correction | preference | exception | procedure",
        ),
        sa.Column(
            "rule_text", sa.Text(), nullable=False, comment="Human-readable rule description."
        ),
        sa.Column(
            "source",
            sa.String(50),
            nullable=False,
            comment="human_correction | manual_entry | system_learned",
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["ca_firm_id"], ["ca_firms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="SET NULL"),
    )
    # Add vector column separately (Alembic doesn't natively handle pgvector types)
    op.execute("ALTER TABLE ca_firm_rules ADD COLUMN rule_embedding vector(1024)")

    # Partial index for active rules lookup
    op.execute("""
        CREATE INDEX idx_firm_rules_lookup
        ON ca_firm_rules (ca_firm_id, client_id)
        WHERE is_active = TRUE
    """)

    # IVFFlat index for cosine similarity search
    op.execute("""
        CREATE INDEX idx_firm_rules_vector
        ON ca_firm_rules USING ivfflat (rule_embedding vector_cosine_ops)
        WITH (lists = 100)
    """)

    # ==================================================================
    # TABLE: conversations
    # ==================================================================
    op.create_table(
        "conversations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("ca_firm_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "client_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="NULL if no client context active.",
        ),
        sa.Column("role", sa.String(20), nullable=False, comment="user | assistant | system"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "message_id",
            sa.BigInteger(),
            nullable=True,
            comment="Telegram message_id — NULL for system-generated turns.",
        ),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["ca_firm_id"], ["ca_firms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "idx_conversations_lookup", "conversations", ["ca_firm_id", "chat_id", "created_at"]
    )

    # ==================================================================
    # TABLE: tasks
    # ==================================================================
    op.create_table(
        "tasks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("ca_firm_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            server_default=sa.text("'pending'"),
            nullable=False,
            comment="pending | in_progress | completed | cancelled",
        ),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["ca_firm_id"], ["ca_firms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="SET NULL"),
    )
    op.execute("""
        CREATE INDEX idx_tasks_due
        ON tasks (ca_firm_id, status, due_at)
        WHERE status = 'pending'
    """)

    # ==================================================================
    # TABLE: filing_approvals (immutable audit trail)
    # ==================================================================
    op.create_table(
        "filing_approvals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("ca_firm_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filing_month", sa.Integer(), nullable=False),
        sa.Column("filing_year", sa.Integer(), nullable=False),
        sa.Column("approved_by_chat_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "filing_snapshot",
            postgresql.JSONB(),
            nullable=False,
            comment="Full state at time of approval — document IDs, totals, etc.",
        ),
        sa.Column(
            "approval_hash",
            sa.Text(),
            nullable=False,
            comment="SHA-256 of the filing_snapshot for integrity verification.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["ca_firm_id"], ["ca_firms.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "ca_firm_id", "filing_month", "filing_year", name="uq_filing_approvals_firm_period"
        ),
    )

    # ==================================================================
    # TABLE: idempotency_log
    # ==================================================================
    op.create_table(
        "idempotency_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("ca_firm_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("update_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "payload_hash",
            sa.Text(),
            nullable=False,
            comment="SHA-256 of the base64-decoded JSON envelope.",
        ),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "message_id", name="uq_idempotency_chat_msg"),
    )
    op.create_index("idx_idempotency_lookup", "idempotency_log", ["chat_id", "message_id"])

    # ==================================================================
    # TABLE: identity_resolution_log
    # ==================================================================
    op.create_table(
        "identity_resolution_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("ca_firm_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "stage_reached",
            sa.Integer(),
            nullable=False,
            comment="1=session, 2=caption, 3=gstin, 4=vendor, 5=explicit_ask",
        ),
        sa.Column("matched_client_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confidence", sa.String(20), nullable=True, comment="HIGH | MEDIUM | EXPLICIT"),
        sa.Column("resolution_time_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["ca_firm_id"], ["ca_firms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["matched_client_id"], ["clients.id"], ondelete="SET NULL"),
    )

    # ==================================================================
    # TABLE: client_notifications
    # ==================================================================
    op.create_table(
        "client_notifications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("ca_firm_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "notification_type",
            sa.String(50),
            nullable=False,
            comment="document_chase | deadline_reminder | filing_reminder",
        ),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False
        ),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["ca_firm_id"], ["ca_firms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "idx_notifications_cooldown",
        "client_notifications",
        ["ca_firm_id", "client_id", "notification_type", "cooldown_until"],
    )

    # ==================================================================
    # ROW-LEVEL SECURITY POLICIES (defense in depth)
    # ==================================================================
    _rls_tables = [
        "clients",
        "documents",
        "ca_firm_rules",
        "conversations",
        "tasks",
        "filing_approvals",
        "client_notifications",
        "identity_resolution_log",
    ]

    for table in _rls_tables:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
                FOR ALL
                USING (ca_firm_id = current_setting('app.current_firm_id', true)::uuid)
                WITH CHECK (ca_firm_id = current_setting('app.current_firm_id', true)::uuid)
        """)


def downgrade() -> None:
    # Drop RLS policies first
    _rls_tables = [
        "clients",
        "documents",
        "ca_firm_rules",
        "conversations",
        "tasks",
        "filing_approvals",
        "client_notifications",
        "identity_resolution_log",
    ]
    for table in _rls_tables:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # Drop tables in reverse dependency order
    op.drop_table("client_notifications")
    op.drop_table("identity_resolution_log")
    op.drop_table("idempotency_log")
    op.drop_table("filing_approvals")
    op.drop_table("tasks")
    op.drop_table("conversations")
    op.drop_table("ca_firm_rules")
    op.drop_table("documents")
    op.drop_table("clients")
    op.drop_table("firm_users")
    op.drop_table("ca_firms")
