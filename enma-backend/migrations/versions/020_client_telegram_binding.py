"""020 — per-client Telegram 1:1 chat binding (Phase 8a, ADR-017).

The client document-ingestion channel: a client taps a per-client deep link
(``t.me/<bot>?start=client_<uuid>``) which binds *that* 1:1 chat to the client
— deterministic routing, no groups. ``clients.telegram_chat_id`` stores the
bound chat; a partial-unique index keeps one chat bound to at most one client.

Nullable — a client without a bound chat simply has no Telegram channel yet.

Idempotent. Revises 019.

Revision ID: 020
Revises: 019
Create Date: 2026-06-27
"""

from __future__ import annotations

from alembic import op

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS telegram_chat_id BIGINT"
    )
    # One Telegram chat binds at most one client (partial — nulls don't clash).
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_clients_telegram_chat_id
            ON clients (telegram_chat_id)
            WHERE telegram_chat_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_clients_telegram_chat_id")
    op.execute("ALTER TABLE clients DROP COLUMN IF EXISTS telegram_chat_id")
