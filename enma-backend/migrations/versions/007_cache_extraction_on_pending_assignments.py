"""007 — R1: cache extract_only() output on pending_assignments.

Refactor R1 of the autonomous-routing architecture splits the document
pipeline into ``extract_only`` (file_processor → classifier → extractor)
and ``finalize_document`` (tax engine + persistence). R2 will run
``extract_only`` *before* identity resolution so the resolver can route
on real buyer fields. When routing still cannot decide and falls back to
EXPLICIT_ASK, we cache the extraction JSON on the pending_assignments
row so the eventual ``/assign`` (or inline-button) confirmation can run
``finalize_document`` directly — no second LLM round trip.

This migration only adds the storage. The R1 deploy leaves the columns
``NULL`` for every row; R2 starts populating them.

Both columns are ``NULLABLE`` so legacy rows created before this
migration (and any R1 rows) are valid.

Revision ID: 007
Revises: 006
Create Date: 2026-06-13
"""

from __future__ import annotations

from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent — production may already be at HEAD via a manual fix
    # window, so guard the DDL the same way migration 006 does.
    op.execute(
        """
        ALTER TABLE pending_assignments
            ADD COLUMN IF NOT EXISTS extraction JSONB
        """
    )
    op.execute(
        """
        ALTER TABLE pending_assignments
            ADD COLUMN IF NOT EXISTS extraction_document_type VARCHAR(64)
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE pending_assignments DROP COLUMN IF EXISTS extraction_document_type"
    )
    op.execute("ALTER TABLE pending_assignments DROP COLUMN IF EXISTS extraction")
