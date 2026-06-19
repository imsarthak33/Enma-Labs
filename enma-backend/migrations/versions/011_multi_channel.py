"""011 — P1: multi-channel (WhatsApp + Telegram) schema.

Phase 1 of the dual-channel architecture. Existing Telegram firms keep
working unchanged — we ONLY add columns; nothing is renamed or
dropped. The new ``primary_channel`` enum picks which set of
credentials the routing code consults at egress time.

What this migration adds
------------------------
On ``ca_firms``:
  * ``primary_channel``           — 'telegram' | 'whatsapp', default 'telegram'
  * ``whatsapp_provider``         — 'twilio' | 'meta_cloud' (NULL until firm onboards WA)
  * ``whatsapp_account_id``       — Twilio account_sid OR Meta WABA id
  * ``whatsapp_phone_number``     — E.164 sender (or Twilio sandbox number)
  * ``whatsapp_auth_token_encrypted`` — Fernet-encrypted credential bytes
  * ``whatsapp_linked_at``        — when the firm completed WA onboarding

On ``firm_users``:
  * ``primary_channel``           — 'telegram' | 'whatsapp' | NULL=inherit
  * ``whatsapp_phone_number``     — this user's E.164 number Enma sends to
  * ``chat_id`` becomes NULLABLE  — WhatsApp-only users have no Telegram chat
  * Partial unique index on ``(ca_firm_id, whatsapp_phone_number)``

CHECK constraints enforce valid enum values for ``primary_channel``
and ``whatsapp_provider``. All DDL is ``IF NOT EXISTS``-guarded /
EXCEPTION-tolerant so production re-runs are no-ops.

Per-firm credentials use Fernet (symmetric AES-128-CBC + HMAC-SHA256)
with the key in the task def env (``ENMA_CRYPTO_KEY``). KMS-wrapped
Fernet is a W5+ follow-up — same upgrade path, no schema change
needed.

Revision ID: 011
Revises: 010
Create Date: 2026-06-16
"""

from __future__ import annotations

from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # ca_firms — channel + WhatsApp credentials
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE ca_firms
            ADD COLUMN IF NOT EXISTS primary_channel VARCHAR(20)
                NOT NULL DEFAULT 'telegram'
        """
    )
    op.execute(
        "ALTER TABLE ca_firms ADD COLUMN IF NOT EXISTS whatsapp_provider VARCHAR(20)"
    )
    op.execute(
        "ALTER TABLE ca_firms ADD COLUMN IF NOT EXISTS whatsapp_account_id TEXT"
    )
    op.execute(
        "ALTER TABLE ca_firms ADD COLUMN IF NOT EXISTS whatsapp_phone_number TEXT"
    )
    op.execute(
        "ALTER TABLE ca_firms ADD COLUMN IF NOT EXISTS whatsapp_auth_token_encrypted BYTEA"
    )
    op.execute(
        "ALTER TABLE ca_firms ADD COLUMN IF NOT EXISTS whatsapp_linked_at TIMESTAMPTZ"
    )
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE ca_firms
                ADD CONSTRAINT ck_ca_firms_primary_channel
                CHECK (primary_channel IN ('telegram', 'whatsapp'));
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
            ALTER TABLE ca_firms
                ADD CONSTRAINT ck_ca_firms_whatsapp_provider
                CHECK (whatsapp_provider IS NULL
                       OR whatsapp_provider IN ('twilio', 'meta_cloud'));
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )

    # ------------------------------------------------------------------
    # firm_users — per-user channel preference + WhatsApp phone
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE firm_users ADD COLUMN IF NOT EXISTS primary_channel VARCHAR(20)"
    )
    op.execute(
        "ALTER TABLE firm_users ADD COLUMN IF NOT EXISTS whatsapp_phone_number TEXT"
    )
    # chat_id must become NULLABLE so WhatsApp-only users can exist
    # without a Telegram identifier. Postgres semantics already permit
    # multiple NULLs under the existing uq_firm_users_firm_chat unique
    # constraint, so flipping the column is safe.
    op.execute(
        "ALTER TABLE firm_users ALTER COLUMN chat_id DROP NOT NULL"
    )
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE firm_users
                ADD CONSTRAINT ck_firm_users_primary_channel
                CHECK (primary_channel IS NULL
                       OR primary_channel IN ('telegram', 'whatsapp'));
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_firm_users_firm_whatsapp
            ON firm_users (ca_firm_id, whatsapp_phone_number)
            WHERE whatsapp_phone_number IS NOT NULL
        """
    )


def downgrade() -> None:
    # firm_users
    op.execute("DROP INDEX IF EXISTS uq_firm_users_firm_whatsapp")
    op.execute("ALTER TABLE firm_users DROP CONSTRAINT IF EXISTS ck_firm_users_primary_channel")
    # Don't restore the NOT NULL on chat_id — production rows may have
    # been re-onboarded with NULL chat_id since the upgrade.
    op.execute("ALTER TABLE firm_users DROP COLUMN IF EXISTS whatsapp_phone_number")
    op.execute("ALTER TABLE firm_users DROP COLUMN IF EXISTS primary_channel")

    # ca_firms
    op.execute("ALTER TABLE ca_firms DROP CONSTRAINT IF EXISTS ck_ca_firms_whatsapp_provider")
    op.execute("ALTER TABLE ca_firms DROP CONSTRAINT IF EXISTS ck_ca_firms_primary_channel")
    op.execute("ALTER TABLE ca_firms DROP COLUMN IF EXISTS whatsapp_linked_at")
    op.execute("ALTER TABLE ca_firms DROP COLUMN IF EXISTS whatsapp_auth_token_encrypted")
    op.execute("ALTER TABLE ca_firms DROP COLUMN IF EXISTS whatsapp_phone_number")
    op.execute("ALTER TABLE ca_firms DROP COLUMN IF EXISTS whatsapp_account_id")
    op.execute("ALTER TABLE ca_firms DROP COLUMN IF EXISTS whatsapp_provider")
    op.execute("ALTER TABLE ca_firms DROP COLUMN IF EXISTS primary_channel")
