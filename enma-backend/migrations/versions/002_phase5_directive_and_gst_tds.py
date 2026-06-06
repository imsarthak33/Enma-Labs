"""002 — Phase 5: ca_firm_rules.directive + clients.gst_tds_deductor.

Adds the two columns Phase 5 needs (ADR-005 rev 2):

  * ``ca_firm_rules.directive JSONB NOT NULL DEFAULT '{}'`` — authoritative,
    typed override directive the tax engine applies deterministically.
    Carries a ``CHECK (jsonb_typeof(directive) = 'object')`` constraint so
    even raw SQL writes can't insert a non-object payload.
  * ``clients.gst_tds_deductor BOOLEAN NOT NULL DEFAULT FALSE`` — flags a
    client as a Section 51 GST-TDS deductor (government / PSU). Engine
    gates ``tds_amount`` on this flag.

Income-Tax Act TDS (194C / 194J / …) is out of scope for v1.

Revision ID: 002
Revises: 001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # ca_firm_rules.directive
    # ------------------------------------------------------------------
    op.add_column(
        "ca_firm_rules",
        sa.Column(
            "directive",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment=(
                "Typed RuleDirective payload (action + scope). Authoritative for "
                "the tax engine; rule_text is retrieval-only."
            ),
        ),
    )
    op.create_check_constraint(
        "ck_ca_firm_rules_directive_is_object",
        "ca_firm_rules",
        "jsonb_typeof(directive) = 'object'",
    )

    # ------------------------------------------------------------------
    # clients.gst_tds_deductor
    # ------------------------------------------------------------------
    op.add_column(
        "clients",
        sa.Column(
            "gst_tds_deductor",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("FALSE"),
            comment=(
                "Section 51 GST-TDS deductor flag (government / PSU). "
                "Engine gates tds_amount on this. IT-Act TDS is out of scope."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("clients", "gst_tds_deductor")
    op.drop_constraint(
        "ck_ca_firm_rules_directive_is_object",
        "ca_firm_rules",
        type_="check",
    )
    op.drop_column("ca_firm_rules", "directive")
