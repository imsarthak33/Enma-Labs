"""GST 2.0 rate master — Part 3 of the Tax Knowledge Base.

Post-22 September 2025 (56th GST Council / GST 2.0) rate structure.
The old five-tier system (0/5/12/18/28% + cess) collapsed into a
simplified structure: 0 / 5 / 18 / 40% plus niche legacy rates
(3% gold, 0.25% rough precious stones) and composition rates (1–6%).

Architectural principle
-----------------------
**Every rate is DATA, not code.**  Indian tax law changes by GST
Council notification multiple times per year.  The rate table lives in
a DB table (``gst_rate_master``) with ``effective_from`` and
``effective_to`` columns so a future rate change is a data migration,
not a code deploy.

This module provides:
  * ``GstRateEntry`` — the typed shape of one row.
  * ``GST_RATE_SEED_DATA`` — the full seed dataset (used by the Alembic
    migration and by unit tests without a DB).
  * ``get_rate_for_hsn()`` — in-memory lookup from seed data; the
    production path will query the DB table instead.

Nothing here touches the database, HTTP, or any LLM client.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

__all__ = [
    "GST_RATE_SEED_DATA",
    "GstRateEntry",
    "get_rate_for_hsn",
]


# ---------------------------------------------------------------------------
# GST 2.0 effective date — the 56th Council reform.
# ---------------------------------------------------------------------------

GST_2_0_EFFECTIVE: Final[date] = date(2025, 9, 22)


# ---------------------------------------------------------------------------
# Typed row shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GstRateEntry:
    """One row in the ``gst_rate_master`` table.

    ``hsn_prefix`` is the leading characters of an HSN/SAC code that
    triggers this rate.  Multiple entries can share an HSN prefix if
    the rate depends on a sub-condition (e.g. apparel price threshold).
    """

    hsn_prefix: str
    description: str
    category: str
    gst_rate: Decimal
    notes: str
    effective_from: date
    effective_to: date | None = None   # None = currently in force


# ---------------------------------------------------------------------------
# Seed data — Part 3.2 category table
# ---------------------------------------------------------------------------

_EFF = GST_2_0_EFFECTIVE   # shorthand for the common effective date

GST_RATE_SEED_DATA: Final[tuple[GstRateEntry, ...]] = (

    # ── Textiles & Apparel ──────────────────────────────────────────────
    GstRateEntry("5201", "Raw cotton", "Textiles", Decimal("5"), "RCM applies — see Part 6", _EFF),
    GstRateEntry("5402", "Man-made fibre (MMF)", "Textiles", Decimal("5"), "Reduced from 18%", _EFF),
    GstRateEntry("5509", "Man-made / synthetic yarn", "Textiles", Decimal("5"), "Reduced from 12%", _EFF),
    GstRateEntry("5205", "Cotton yarn / cotton waste yarn", "Textiles", Decimal("5"), "", _EFF),
    GstRateEntry("52", "Fabric (woven, knitted)", "Textiles", Decimal("5"), "", _EFF),
    GstRateEntry("6101", "Apparel — price ≤ ₹2,500/piece", "Textiles", Decimal("5"), "Threshold raised from ₹1,000", _EFF),
    GstRateEntry("6102", "Apparel — price > ₹2,500/piece", "Textiles", Decimal("18"), "Raised from 12%", _EFF),
    GstRateEntry("6401", "Footwear — price ≤ ₹2,500/pair", "Textiles", Decimal("5"), "", _EFF),
    GstRateEntry("6402", "Footwear — price > ₹2,500/pair", "Textiles", Decimal("18"), "", _EFF),
    GstRateEntry("96039", "Made-up textile articles (mops, quilts, cotton dori products)", "Textiles", Decimal("18"), "Raised from 12% — Cotton Dori Mop category", _EFF),
    GstRateEntry("5701", "Carpets & floor coverings", "Textiles", Decimal("5"), "Reduced from 12%", _EFF),
    GstRateEntry("5702", "Carpets & floor coverings (woven)", "Textiles", Decimal("5"), "Reduced from 12%", _EFF),
    GstRateEntry("5703", "Carpets & floor coverings (tufted)", "Textiles", Decimal("5"), "Reduced from 12%", _EFF),
    GstRateEntry("5704", "Carpets & floor coverings (felt)", "Textiles", Decimal("5"), "Reduced from 12%", _EFF),
    GstRateEntry("5705", "Carpets & floor coverings (other)", "Textiles", Decimal("5"), "Reduced from 12%", _EFF),
    GstRateEntry("8452", "Sewing machines", "Textiles", Decimal("5"), "Reduced from 12%", _EFF),

    # ── FMCG & Daily Essentials ─────────────────────────────────────────
    GstRateEntry("3401", "Soaps, personal care", "FMCG", Decimal("5"), "Reduced from 18%", _EFF),
    GstRateEntry("3305", "Shampoos", "FMCG", Decimal("5"), "Reduced from 18%", _EFF),
    GstRateEntry("3306", "Toothpaste", "FMCG", Decimal("5"), "Reduced from 18%", _EFF),
    GstRateEntry("2106", "Packaged food items", "FMCG", Decimal("5"), "", _EFF),
    GstRateEntry("0401", "Dairy products (specified)", "FMCG", Decimal("0"), "Exempt", _EFF),
    GstRateEntry("0406", "Paneer", "FMCG", Decimal("0"), "Exempt", _EFF),
    GstRateEntry("1905", "Roti, paratha (specified breads)", "FMCG", Decimal("0"), "Exempt", _EFF),
    GstRateEntry("1507", "Edible oils (soybean)", "FMCG", Decimal("5"), "", _EFF),
    GstRateEntry("1511", "Edible oils (palm)", "FMCG", Decimal("5"), "", _EFF),
    GstRateEntry("1512", "Edible oils (sunflower/safflower)", "FMCG", Decimal("5"), "", _EFF),
    GstRateEntry("1515", "Edible oils (other)", "FMCG", Decimal("5"), "", _EFF),

    # ── Healthcare & Pharma ─────────────────────────────────────────────
    GstRateEntry("3003", "Drugs/medicines (general)", "Healthcare", Decimal("5"), "Concessional rate", _EFF),
    GstRateEntry("3004", "Drugs/medicines (dosaged)", "Healthcare", Decimal("5"), "Concessional rate", _EFF),
    GstRateEntry("3002", "Lifesaving drugs (specified)", "Healthcare", Decimal("0"), "33 specified lifesaving drugs", _EFF),
    GstRateEntry("9971", "Individual health & life insurance premiums", "Healthcare", Decimal("0"), "Exempted under GST 2.0", _EFF),
    GstRateEntry("9018", "Medical devices (general)", "Healthcare", Decimal("5"), "", _EFF),

    # ── Electronics, Durables & Automobiles ─────────────────────────────
    GstRateEntry("8528", "Consumer electronics (TV)", "Electronics", Decimal("18"), "Reduced from 28%", _EFF),
    GstRateEntry("8415", "Consumer electronics (AC)", "Electronics", Decimal("18"), "Reduced from 28%", _EFF),
    GstRateEntry("8450", "Consumer electronics (washing machine)", "Electronics", Decimal("18"), "Reduced from 28%", _EFF),
    GstRateEntry("8418", "Consumer electronics (refrigerator)", "Electronics", Decimal("18"), "Reduced from 28%", _EFF),
    GstRateEntry("8703", "Small cars & motorcycles (≤350cc)", "Automobiles", Decimal("18"), "Reduced from 28% + cess", _EFF),
    GstRateEntry("8703L", "Large/luxury vehicles", "Automobiles", Decimal("40"), "New de-merit slab", _EFF),
    GstRateEntry("8711", "Electric vehicles (EVs)", "Automobiles", Decimal("5"), "Retained — no cess", _EFF),
    GstRateEntry("2523", "Cement", "Construction", Decimal("18"), "Reduced from 28%", _EFF),
    GstRateEntry("8714", "Auto parts, bicycles", "Automobiles", Decimal("18"), "", _EFF),

    # ── Hospitality, Restaurants & Personal Services ────────────────────
    GstRateEntry("9963R", "Restaurant services (standalone)", "Hospitality", Decimal("5"), "No ITC", _EFF),
    GstRateEntry("9963H5", "Hotel accommodation ≤ ₹7,500/unit/day", "Hospitality", Decimal("5"), "Reduced from 12%", _EFF),
    GstRateEntry("9963H18", "Hotel accommodation > ₹7,500/unit/day", "Hospitality", Decimal("18"), "", _EFF),
    GstRateEntry("9997B", "Beauty & wellness (salons, gyms, yoga, barbers)", "Services", Decimal("5"), "Reduced from 18%", _EFF),

    # ── Gold, Jewellery & Precious Items ────────────────────────────────
    GstRateEntry("7108", "Gold/jewellery (value of gold)", "Precious", Decimal("3"), "Niche rate, unaffected by GST 2.0", _EFF),
    GstRateEntry("7113", "Jewellery making charges", "Precious", Decimal("5"), "ITC available on this component", _EFF),
    GstRateEntry("7103", "Rough precious/semi-precious stones", "Precious", Decimal("0.25"), "", _EFF),

    # ── Agriculture & Renewable Energy ──────────────────────────────────
    GstRateEntry("2807", "Fertilizer inputs (sulphuric acid)", "Agriculture", Decimal("5"), "Reduced from 18%", _EFF),
    GstRateEntry("2808", "Fertilizer inputs (nitric acid)", "Agriculture", Decimal("5"), "Reduced from 18%", _EFF),
    GstRateEntry("2814", "Fertilizer inputs (ammonia)", "Agriculture", Decimal("5"), "Reduced from 18%", _EFF),
    GstRateEntry("8432", "Agricultural machinery", "Agriculture", Decimal("5"), "", _EFF),
    GstRateEntry("8541", "Solar power generators", "Renewable", Decimal("5"), "Reduced from 12%", _EFF),
    GstRateEntry("8412", "Windmills, waste-to-energy systems", "Renewable", Decimal("5"), "Reduced from 12%", _EFF),

    # ── Sin & Luxury Goods (40% de-merit slab) ──────────────────────────
    # NOTE: Tobacco/pan masala stays on legacy 28%+cess until compensation-
    # cess loan discharge — do NOT hardcode 40% for tobacco yet.
    GstRateEntry("2202", "Aerated & caffeinated beverages", "Sin", Decimal("40"), "", _EFF),
    GstRateEntry("8903", "Yachts, private jets", "Sin", Decimal("40"), "", _EFF),

    # ── Construction & Real Estate ──────────────────────────────────────
    GstRateEntry("9954A", "Under-construction residential (affordable housing)", "RealEstate", Decimal("1"), "No ITC, unchanged by GST 2.0", _EFF),
    GstRateEntry("9954N", "Under-construction residential (non-affordable)", "RealEstate", Decimal("5"), "No ITC, unchanged by GST 2.0", _EFF),
    GstRateEntry("9954W", "Works contract services (general)", "RealEstate", Decimal("18"), "", _EFF),

    # ── Professional & Financial Services ───────────────────────────────
    GstRateEntry("9982", "Professional/technical/consulting services", "Services", Decimal("18"), "Standard rate post-reform", _EFF),
    GstRateEntry("9971L", "Loan processing / financial services", "Services", Decimal("18"), "", _EFF),
    GstRateEntry("9971I", "General insurance (motor, fire, etc.)", "Services", Decimal("18"), "", _EFF),

    # ── Tailoring / Job-work ────────────────────────────────────────────
    GstRateEntry("9988", "Tailoring / job-work services", "Services", Decimal("5"), "Reduced from 18%", _EFF),
)


# ---------------------------------------------------------------------------
# In-memory lookup — used by unit tests and as a fallback.
# Production will query the ``gst_rate_master`` DB table.
# ---------------------------------------------------------------------------


def get_rate_for_hsn(
    hsn: str,
    as_of_date: date | None = None,
    *,
    seed_data: tuple[GstRateEntry, ...] = GST_RATE_SEED_DATA,
) -> GstRateEntry | None:
    """Look up the applicable GST rate entry for an HSN/SAC code.

    Matches by longest-prefix-first against the seed data (or the
    provided data table).  If ``as_of_date`` is given, only entries
    whose ``[effective_from, effective_to]`` window contains the date
    are considered.

    Returns ``None`` when no matching entry is found.
    """
    if not hsn or not isinstance(hsn, str):
        return None
    clean = hsn.strip()

    # Sort candidates by prefix length descending — longest match wins.
    candidates: list[GstRateEntry] = []
    for entry in seed_data:
        if clean.startswith(entry.hsn_prefix):
            if as_of_date is not None:
                if entry.effective_from > as_of_date:
                    continue
                if entry.effective_to is not None and entry.effective_to < as_of_date:
                    continue
            candidates.append(entry)

    if not candidates:
        return None

    # Longest prefix wins (most specific match).
    candidates.sort(key=lambda e: len(e.hsn_prefix), reverse=True)
    return candidates[0]
