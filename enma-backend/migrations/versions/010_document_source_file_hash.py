"""010 — W3.5-d: source-file hash for pre-extraction dedup.

Content-hash dedup (migration 009) works at the persistence step but
only after extraction has run. Production saw a re-upload of invoice
91 cost a layout + extraction LLM call (~$0.013 + 63s wall clock)
before the dedup short-circuit fired (request 69227d42, 2026-06-16).

Adding a SHA-256 of the raw file bytes lets the worker route check
the moment download finishes — before classification or extraction —
and skip the whole pipeline if those exact bytes were seen before
for this firm.

Scope is per-firm rather than per-(firm, client) because we hash
*before* identity resolution; the same bytes are the same real file
regardless of which client the CA ultimately routed it to.

Revision ID: 010
Revises: 009
Create Date: 2026-06-16
"""

from __future__ import annotations

from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_file_hash TEXT"
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_documents_source_file_hash
            ON documents (ca_firm_id, source_file_hash)
            WHERE source_file_hash IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_documents_source_file_hash")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS source_file_hash")
