"""019 — per-client GSP consent token on clients (Phase 7b.2).

The GSTR-2B-via-GSP monthly-pull cron needs, per client, the OTP-consent auth
token GSTN issues (valid ~6h, refreshable ~30 days) and its expiry. These
columns are the storage the pull cron reads; the OTP-consent *acquisition*
flow (which populates them) is the remaining GSP-specific integration.

Both nullable — a client without a stored, unexpired token is simply skipped
by the pull, so the feature stays dormant until consent is captured.

Idempotent: ADD/DROP COLUMN IF [NOT] EXISTS. Revises 018.

Revision ID: 019
Revises: 018
Create Date: 2026-06-27
"""

from __future__ import annotations

from alembic import op

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS gsp_auth_token TEXT")
    op.execute(
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS "
        "gsp_auth_token_expires_at TIMESTAMPTZ"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE clients DROP COLUMN IF EXISTS gsp_auth_token")
    op.execute("ALTER TABLE clients DROP COLUMN IF EXISTS gsp_auth_token_expires_at")
