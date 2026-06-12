"""005 — Align ca_firms with frontend (Next.js + Supabase) onboarding.

The web app now owns firm creation: it inserts a ``ca_firms`` row with
the CA's profile, consents, and Supabase auth id. The Telegram bot then
links that row via ``/start <firm_uuid>``. To make SQLAlchemy reads
honor every column the frontend writes, we add those columns here, and
we relax ``admin_chat_id`` / ``telegram_bot_token`` to NULLABLE because
they get filled in only at link time.

The frontend already adds most of these columns via Supabase Studio, so
every ALTER statement is guarded with IF (NOT) EXISTS to keep the
migration idempotent across environments.

Revision ID: 005
Revises: 004
Create Date: 2026-06-12
"""

from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


# Pairs of (column_name, column_type_with_default). PostgreSQL DDL is
# raw-string here because we need idempotent ADD COLUMN IF NOT EXISTS,
# which isn't surfaced by op.add_column().
_NEW_COLUMNS: list[tuple[str, str]] = [
    ("supabase_user_id", "UUID UNIQUE"),
    ("ca_name", "TEXT"),
    ("email", "TEXT"),
    ("phone", "TEXT"),
    ("telegram_chat_id", "TEXT"),
    ("telegram_linked_at", "TIMESTAMPTZ"),
    ("onboarding_completed", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("dpa_consented", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("dpa_consented_at", "TIMESTAMPTZ"),
    ("privacy_policy_consented", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("privacy_policy_consented_at", "TIMESTAMPTZ"),
    ("data_training_consent", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("is_active", "BOOLEAN NOT NULL DEFAULT TRUE"),
    ("subscription_plan", "VARCHAR(50) NOT NULL DEFAULT 'trial'"),
    ("last_login", "TIMESTAMPTZ"),
    ("known_groups", "JSONB"),
]


def upgrade() -> None:
    for name, type_def in _NEW_COLUMNS:
        op.execute(f'ALTER TABLE ca_firms ADD COLUMN IF NOT EXISTS {name} {type_def}')

    # Frontend creates rows BEFORE Telegram is linked, so these columns
    # must accept NULL until the /start <uuid> handoff completes. The
    # initial migration declared them NOT NULL; relax that.
    op.execute("ALTER TABLE ca_firms ALTER COLUMN admin_chat_id DROP NOT NULL")
    op.execute("ALTER TABLE ca_firms ALTER COLUMN telegram_bot_token DROP NOT NULL")

    # Helpful index for the frontend's primary lookup
    # (where supabase_user_id = $auth.uid()).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ca_firms_supabase_user_id "
        "ON ca_firms (supabase_user_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ca_firms_supabase_user_id")
    # Re-tightening NOT NULL on legacy columns would break frontend-only
    # rows; downgrade leaves them NULLable on purpose.
    for name, _ in reversed(_NEW_COLUMNS):
        op.execute(f"ALTER TABLE ca_firms DROP COLUMN IF EXISTS {name}")
