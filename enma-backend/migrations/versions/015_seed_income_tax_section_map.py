"""015 — Seed: income_tax_section_map — Income Tax Act 2025 TDS transition.

Creates the ``income_tax_section_map`` table and seeds it with the
legacy (Income Tax Act, 1961) → new (Income Tax Act, 2025) TDS section
mapping.  The new Act is effective 1 April 2026.

Key design choices
------------------
* ``legacy_section`` is the stable human-readable key (e.g. "194C").
* ``new_act_reference`` is the Section 392/393 reference under the new Act.
* ``cbdt_payment_code`` is nullable and updatable separately — the exact
  numeric codes are still being clarified in practitioner circles as of
  mid-2026 and should never be hardcoded as ground truth.
* ``effective_from`` on this table means "this row applies to payments on
  or after this date". For legacy rows, effective_from = '2024-04-01'
  (start of FY 2024-25 as the general start); for new Act rows,
  effective_from = '2026-04-01'.

Idempotency
-----------
All DDL is ``IF NOT EXISTS`` guarded. Seed data uses
``ON CONFLICT DO NOTHING`` on the natural key.

Revision ID: 015
Revises: 014
Create Date: 2026-06-23
"""

from __future__ import annotations

from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # income_tax_section_map — versioned TDS section lookup table
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS income_tax_section_map (
            id SERIAL PRIMARY KEY,
            nature_of_payment TEXT NOT NULL,
            legacy_section VARCHAR(20) NOT NULL,
            new_act_reference TEXT NOT NULL,
            rate_percent NUMERIC(5,2) NOT NULL,
            threshold_inr NUMERIC(14,2) NOT NULL DEFAULT 0,
            threshold_notes TEXT NOT NULL DEFAULT '',
            cbdt_payment_code VARCHAR(10),
            notes TEXT NOT NULL DEFAULT '',
            effective_from DATE NOT NULL DEFAULT '2026-04-01',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (nature_of_payment, legacy_section, effective_from)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_income_tax_section_map_nature
            ON income_tax_section_map (nature_of_payment, effective_from DESC)
        """
    )

    # ------------------------------------------------------------------
    # Seed data — Part 4.2 legacy → new Act mapping
    # ------------------------------------------------------------------
    op.execute(
        """
        INSERT INTO income_tax_section_map
            (nature_of_payment, legacy_section, new_act_reference, rate_percent, threshold_inr, threshold_notes, notes, effective_from)
        VALUES
            ('Contractor payments (individual/HUF)', '194C', 'Section 393(1), Table Sl. No. 6(i)', 1.00, 30000,
             '₹30,000 single or ₹1,00,000 aggregate in FY', '', '2026-04-01'),

            ('Contractor payments (others)', '194C', 'Section 393(1), Table Sl. No. 6(i)', 2.00, 30000,
             '₹30,000 single or ₹1,00,000 aggregate in FY', '', '2026-04-01'),

            ('Professional fees', '194J', 'Section 393, Sl. No. 6(i)(b)-equivalent', 10.00, 30000,
             '', '', '2026-04-01'),

            ('Technical services fees', '194J', 'Section 393, Sl. No. 6(i)(b)-equivalent', 2.00, 30000,
             '', '', '2026-04-01'),

            ('Rent (land/building/furniture)', '194I', 'Section 393', 10.00, 50000,
             '₹50,000/month (₹2,40,000/year aggregate convention)', '', '2026-04-01'),

            ('Purchase of goods', '194Q', 'Section 393(1)', 0.10, 5000000,
             '₹50,00,000 aggregate from one seller, AND buyer prior-FY turnover > ₹10 crore', '', '2026-04-01'),

            ('Payments to partners (remuneration/interest/bonus)', '194T', 'Section 393', 10.00, 20000,
             '₹20,000 aggregate in FY',
             'New from Finance Act 2024, effective 1 Apr 2025. 20% if no PAN. Deposit by 7th of following month. Form 140 (quarterly).',
             '2026-04-01'),

            ('Salary', '192', 'Section 392', 0.00, 0,
             'Basic exemption threshold', 'Rate per income tax slab, not flat %', '2026-04-01'),

            ('Interest (other than securities)', '194A', 'Section 393', 10.00, 40000,
             '₹40,000 (₹50,000 for senior citizens)', '', '2026-04-01'),

            ('Non-resident payments', '195', 'Section 393 (cross-border table)', 0.00, 0,
             'Nil — applies from first rupee', 'Rate per DTAA or specified schedule', '2026-04-01')
        ON CONFLICT (nature_of_payment, legacy_section, effective_from) DO NOTHING
        """
    )

    # ------------------------------------------------------------------
    # Form name mapping — Part 4.4 (old → new form names)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS income_tax_form_names (
            id SERIAL PRIMARY KEY,
            old_form VARCHAR(60) NOT NULL,
            new_form VARCHAR(60) NOT NULL,
            purpose TEXT NOT NULL,
            effective_from DATE NOT NULL DEFAULT '2026-04-01',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (old_form, effective_from)
        )
        """
    )
    op.execute(
        """
        INSERT INTO income_tax_form_names (old_form, new_form, purpose, effective_from)
        VALUES
            ('Form 16', 'Form 130', 'Salary TDS certificate', '2026-04-01'),
            ('Form 15G / 15H', 'Form 121', 'Declaration for nil/lower TDS', '2026-04-01'),
            ('Form 26AS-linked PAN TDS certificates', 'Form 141',
             'PAN-based TDS (property, rent, VDA, individual contractor/professional payments)', '2026-04-01')
        ON CONFLICT (old_form, effective_from) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_income_tax_section_map_nature")
    op.execute("DROP TABLE IF EXISTS income_tax_section_map")
    op.execute("DROP TABLE IF EXISTS income_tax_form_names")
