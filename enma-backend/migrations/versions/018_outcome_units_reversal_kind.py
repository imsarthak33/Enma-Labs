"""018 — add 'itc_reversal_risk_inr' to the outcome_units kind CHECK (TA-1).

The 180-day bank reconciliation leg surfaces ITC that must be reversed
under Section 16(2) (no payment within 180 days), but until now produced
no billable trace. TA-1's outcome meter sums that reversal-risk rupee
figure alongside recovered ITC, so the bank leg records it as an
``outcome_units`` row with this new ``kind``. Migration 012 created
``ck_outcome_units_kind`` without it, so we drop and re-add the CHECK with
the value included.

Idempotent: DROP ... IF EXISTS, then ADD guarded by EXCEPTION. Revises 017.

Revision ID: 018
Revises: 017
Create Date: 2026-06-26
"""

from __future__ import annotations

from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None

_KINDS_WITH_REVERSAL = (
    "'itc_recovered_inr', 'itc_reversal_risk_inr', 'filed_return', "
    "'drafted_notice', 'reconciled_period', 'invoice_processed', "
    "'liaison_message_sent', 'tally_export_generated', 'client_ledger_exported'"
)
_KINDS_WITHOUT_REVERSAL = (
    "'itc_recovered_inr', 'filed_return', 'drafted_notice', "
    "'reconciled_period', 'invoice_processed', 'liaison_message_sent', "
    "'tally_export_generated', 'client_ledger_exported'"
)


def upgrade() -> None:
    op.execute("ALTER TABLE outcome_units DROP CONSTRAINT IF EXISTS ck_outcome_units_kind")
    op.execute(
        f"""
        DO $$
        BEGIN
            ALTER TABLE outcome_units
                ADD CONSTRAINT ck_outcome_units_kind
                CHECK (kind IN ({_KINDS_WITH_REVERSAL}));
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE outcome_units DROP CONSTRAINT IF EXISTS ck_outcome_units_kind")
    op.execute(
        f"""
        DO $$
        BEGIN
            ALTER TABLE outcome_units
                ADD CONSTRAINT ck_outcome_units_kind
                CHECK (kind IN ({_KINDS_WITHOUT_REVERSAL}));
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )
