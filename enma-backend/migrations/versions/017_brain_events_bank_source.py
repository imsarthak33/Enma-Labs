"""017 — add 'bank' to the brain_events source CHECK (ADR-016).

The bank/180-day reconciliation leg ingests bank-statement rows into
``brain_events`` with ``source='bank'``. Migration 013 created the
``ck_brain_events_source`` CHECK without that value, so we drop and
re-add it with ``'bank'`` included.

Idempotent: DROP ... IF EXISTS, then ADD guarded by EXCEPTION. Revises 016.

Revision ID: 017
Revises: 016
Create Date: 2026-06-24
"""

from __future__ import annotations

from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None

_SOURCES_WITH_BANK = (
    "'tally', 'gmail', 'whatsapp_group', 'gstn_portal', 'enma_internal', 'bank'"
)
_SOURCES_WITHOUT_BANK = (
    "'tally', 'gmail', 'whatsapp_group', 'gstn_portal', 'enma_internal'"
)


def upgrade() -> None:
    op.execute("ALTER TABLE brain_events DROP CONSTRAINT IF EXISTS ck_brain_events_source")
    op.execute(
        f"""
        DO $$
        BEGIN
            ALTER TABLE brain_events
                ADD CONSTRAINT ck_brain_events_source
                CHECK (source IN ({_SOURCES_WITH_BANK}));
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE brain_events DROP CONSTRAINT IF EXISTS ck_brain_events_source")
    op.execute(
        f"""
        DO $$
        BEGIN
            ALTER TABLE brain_events
                ADD CONSTRAINT ck_brain_events_source
                CHECK (source IN ({_SOURCES_WITHOUT_BANK}));
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )
