"""014 — Seed: gst_rate_master — GST 2.0 post-reform rate table.

Creates the ``gst_rate_master`` table and seeds it with the full
post-22-September-2025 (56th GST Council / GST 2.0) rate structure.
Every rate has ``effective_from`` / ``effective_to`` columns so a future
GST Council rate change is a data migration, not a code deploy.

The seed covers the categories most relevant to a general CA practice:
textiles, FMCG, healthcare, electronics/auto, hospitality, gold,
agriculture, sin goods, real estate, and professional services.

Idempotency
-----------
All DDL is ``IF NOT EXISTS`` guarded. Seed data uses
``ON CONFLICT DO NOTHING`` on the natural key (hsn_prefix, effective_from)
so re-running the migration is a no-op.

Revision ID: 014
Revises: 013
Create Date: 2026-06-23
"""

from __future__ import annotations

from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # gst_rate_master — versioned GST rate lookup table
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gst_rate_master (
            id SERIAL PRIMARY KEY,
            hsn_prefix VARCHAR(20) NOT NULL,
            description TEXT NOT NULL,
            category VARCHAR(60) NOT NULL,
            gst_rate NUMERIC(5,2) NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            effective_from DATE NOT NULL,
            effective_to DATE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (hsn_prefix, effective_from)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_gst_rate_master_hsn
            ON gst_rate_master (hsn_prefix, effective_from DESC)
        """
    )

    # ------------------------------------------------------------------
    # Seed data — Part 3.2 category table, effective 2025-09-22
    # ------------------------------------------------------------------
    op.execute(
        """
        INSERT INTO gst_rate_master (hsn_prefix, description, category, gst_rate, notes, effective_from)
        VALUES
            -- Textiles & Apparel
            ('5201', 'Raw cotton', 'Textiles', 5.00, 'RCM applies — Section 9(3)', '2025-09-22'),
            ('5402', 'Man-made fibre (MMF)', 'Textiles', 5.00, 'Reduced from 18%', '2025-09-22'),
            ('5509', 'Man-made / synthetic yarn', 'Textiles', 5.00, 'Reduced from 12%', '2025-09-22'),
            ('5205', 'Cotton yarn / cotton waste yarn', 'Textiles', 5.00, '', '2025-09-22'),
            ('52', 'Fabric (woven, knitted)', 'Textiles', 5.00, '', '2025-09-22'),
            ('6101', 'Apparel — price ≤ ₹2,500/piece', 'Textiles', 5.00, 'Threshold raised from ₹1,000', '2025-09-22'),
            ('6102', 'Apparel — price > ₹2,500/piece', 'Textiles', 18.00, 'Raised from 12%', '2025-09-22'),
            ('6401', 'Footwear — price ≤ ₹2,500/pair', 'Textiles', 5.00, '', '2025-09-22'),
            ('6402', 'Footwear — price > ₹2,500/pair', 'Textiles', 18.00, '', '2025-09-22'),
            ('96039', 'Made-up textile articles (mops, quilts, cotton dori)', 'Textiles', 18.00, 'Raised from 12% — Cotton Dori Mop category', '2025-09-22'),
            ('5701', 'Carpets & floor coverings', 'Textiles', 5.00, 'Reduced from 12%', '2025-09-22'),
            ('5702', 'Carpets & floor coverings (woven)', 'Textiles', 5.00, 'Reduced from 12%', '2025-09-22'),
            ('5703', 'Carpets & floor coverings (tufted)', 'Textiles', 5.00, 'Reduced from 12%', '2025-09-22'),
            ('5704', 'Carpets & floor coverings (felt)', 'Textiles', 5.00, 'Reduced from 12%', '2025-09-22'),
            ('5705', 'Carpets & floor coverings (other)', 'Textiles', 5.00, 'Reduced from 12%', '2025-09-22'),
            ('8452', 'Sewing machines', 'Textiles', 5.00, 'Reduced from 12%', '2025-09-22'),

            -- FMCG & Daily Essentials
            ('3401', 'Soaps, personal care', 'FMCG', 5.00, 'Reduced from 18%', '2025-09-22'),
            ('3305', 'Shampoos', 'FMCG', 5.00, 'Reduced from 18%', '2025-09-22'),
            ('3306', 'Toothpaste', 'FMCG', 5.00, 'Reduced from 18%', '2025-09-22'),
            ('2106', 'Packaged food items', 'FMCG', 5.00, '', '2025-09-22'),
            ('0401', 'Dairy products (specified)', 'FMCG', 0.00, 'Exempt', '2025-09-22'),
            ('0406', 'Paneer', 'FMCG', 0.00, 'Exempt', '2025-09-22'),
            ('1905', 'Roti, paratha (specified breads)', 'FMCG', 0.00, 'Exempt', '2025-09-22'),
            ('1507', 'Edible oils (soybean)', 'FMCG', 5.00, '', '2025-09-22'),
            ('1511', 'Edible oils (palm)', 'FMCG', 5.00, '', '2025-09-22'),
            ('1512', 'Edible oils (sunflower/safflower)', 'FMCG', 5.00, '', '2025-09-22'),
            ('1515', 'Edible oils (other)', 'FMCG', 5.00, '', '2025-09-22'),

            -- Healthcare & Pharma
            ('3003', 'Drugs/medicines (general)', 'Healthcare', 5.00, 'Concessional rate', '2025-09-22'),
            ('3004', 'Drugs/medicines (dosaged)', 'Healthcare', 5.00, 'Concessional rate', '2025-09-22'),
            ('3002', 'Lifesaving drugs (specified)', 'Healthcare', 0.00, '33 specified lifesaving drugs', '2025-09-22'),
            ('9971', 'Individual health & life insurance premiums', 'Healthcare', 0.00, 'Exempted under GST 2.0', '2025-09-22'),
            ('9018', 'Medical devices (general)', 'Healthcare', 5.00, '', '2025-09-22'),

            -- Electronics, Durables & Automobiles
            ('8528', 'Consumer electronics (TV)', 'Electronics', 18.00, 'Reduced from 28%', '2025-09-22'),
            ('8415', 'Consumer electronics (AC)', 'Electronics', 18.00, 'Reduced from 28%', '2025-09-22'),
            ('8450', 'Consumer electronics (washing machine)', 'Electronics', 18.00, 'Reduced from 28%', '2025-09-22'),
            ('8418', 'Consumer electronics (refrigerator)', 'Electronics', 18.00, 'Reduced from 28%', '2025-09-22'),
            ('8703', 'Small cars & motorcycles (≤350cc)', 'Automobiles', 18.00, 'Reduced from 28% + cess', '2025-09-22'),
            ('8703L', 'Large/luxury vehicles', 'Automobiles', 40.00, 'New de-merit slab', '2025-09-22'),
            ('8711', 'Electric vehicles (EVs)', 'Automobiles', 5.00, 'Retained — no cess', '2025-09-22'),
            ('2523', 'Cement', 'Construction', 18.00, 'Reduced from 28%', '2025-09-22'),
            ('8714', 'Auto parts, bicycles', 'Automobiles', 18.00, '', '2025-09-22'),

            -- Hospitality, Restaurants & Personal Services
            ('9963R', 'Restaurant services (standalone)', 'Hospitality', 5.00, 'No ITC', '2025-09-22'),
            ('9963H5', 'Hotel accommodation ≤ ₹7,500/unit/day', 'Hospitality', 5.00, 'Reduced from 12%', '2025-09-22'),
            ('9963H18', 'Hotel accommodation > ₹7,500/unit/day', 'Hospitality', 18.00, '', '2025-09-22'),
            ('9997B', 'Beauty & wellness', 'Services', 5.00, 'Reduced from 18%', '2025-09-22'),

            -- Gold, Jewellery & Precious Items
            ('7108', 'Gold/jewellery (value of gold)', 'Precious', 3.00, 'Niche rate, unaffected by GST 2.0', '2025-09-22'),
            ('7113', 'Jewellery making charges', 'Precious', 5.00, 'ITC available on this component', '2025-09-22'),
            ('7103', 'Rough precious/semi-precious stones', 'Precious', 0.25, '', '2025-09-22'),

            -- Agriculture & Renewable Energy
            ('2807', 'Fertilizer inputs (sulphuric acid)', 'Agriculture', 5.00, 'Reduced from 18%', '2025-09-22'),
            ('2808', 'Fertilizer inputs (nitric acid)', 'Agriculture', 5.00, 'Reduced from 18%', '2025-09-22'),
            ('2814', 'Fertilizer inputs (ammonia)', 'Agriculture', 5.00, 'Reduced from 18%', '2025-09-22'),
            ('8432', 'Agricultural machinery', 'Agriculture', 5.00, '', '2025-09-22'),
            ('8541', 'Solar power generators', 'Renewable', 5.00, 'Reduced from 12%', '2025-09-22'),
            ('8412', 'Windmills, waste-to-energy systems', 'Renewable', 5.00, 'Reduced from 12%', '2025-09-22'),

            -- Sin & Luxury Goods
            ('2202', 'Aerated & caffeinated beverages', 'Sin', 40.00, '', '2025-09-22'),
            ('8903', 'Yachts, private jets', 'Sin', 40.00, '', '2025-09-22'),

            -- Construction & Real Estate
            ('9954A', 'Under-construction residential (affordable)', 'RealEstate', 1.00, 'No ITC, unchanged by GST 2.0', '2025-09-22'),
            ('9954N', 'Under-construction residential (non-affordable)', 'RealEstate', 5.00, 'No ITC, unchanged by GST 2.0', '2025-09-22'),
            ('9954W', 'Works contract services (general)', 'RealEstate', 18.00, '', '2025-09-22'),

            -- Professional & Financial Services
            ('9982', 'Professional/technical/consulting services', 'Services', 18.00, 'Standard rate post-reform', '2025-09-22'),
            ('9971L', 'Loan processing / financial services', 'Services', 18.00, '', '2025-09-22'),
            ('9971I', 'General insurance (motor, fire, etc.)', 'Services', 18.00, '', '2025-09-22'),

            -- Tailoring / Job-work
            ('9988', 'Tailoring / job-work services', 'Services', 5.00, 'Reduced from 18%', '2025-09-22')
        ON CONFLICT (hsn_prefix, effective_from) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_gst_rate_master_hsn")
    op.execute("DROP TABLE IF EXISTS gst_rate_master")
