"""009 — W3-h7: dedup invariant on documents.

Production observed the same real-world invoice being re-uploaded and
re-processed multiple times — three rows for invoice 127, two rows for
invoice 91 — polluting the ledger, the filing-approval snapshot, and
every downstream report. The fix is a content hash on the natural key
of an invoice (vendor_gstin, invoice_number, invoice_date) plus a
pre-INSERT check in :func:`app.agents.pipeline.finalize_document`.

This migration only adds the storage. The runtime dedup check and the
backfill of existing rows are handled separately:

  * Application: ``finalize_document`` computes the hash and skips
    re-processing when a row with the same hash already exists for
    the same ``(ca_firm_id, client_id)``.
  * Backfill / unique constraint: applied as a one-off SQL after the
    operator authorises destruction of the existing duplicates. The
    unique index is intentionally NOT created in this migration —
    adding it before the duplicates are cleaned would fail the
    deploy.

The column is nullable because pre-existing rows have no hash yet and
because the pipeline emits ``NULL`` when one of the three natural-key
fields is missing (very rare but possible: a poorly-extracted PDF).

Revision ID: 009
Revises: 008
Create Date: 2026-06-15
"""

from __future__ import annotations

from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE documents
            ADD COLUMN IF NOT EXISTS content_hash TEXT
        """
    )
    # A non-unique index helps the pre-INSERT lookup. We can convert it
    # to a UNIQUE constraint via a follow-up SQL once duplicates are
    # cleaned up — applying UNIQUE here would fail on the current data.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_documents_content_hash
            ON documents (ca_firm_id, client_id, content_hash)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_documents_content_hash")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS content_hash")
