"""Income Tax Act 2025 TDS section mapping — Part 4 of the Tax Knowledge Base.

The single most important fact in this module
---------------------------------------------
**The Income Tax Act, 1961 was replaced by the Income Tax Act, 2025,
effective 1 April 2026.**  TDS provisions that used to span Sections 192
through 194T are now consolidated into:
  * **Section 392** — Salary TDS (replaces old Section 192)
  * **Section 393** — Everything else (replaces old Sections 193–194T),
    organized as a single table with serial numbers and numeric payment
    codes (1001–1067).

TDS returns filed using old section codes for any payment made on or
after 1 April 2026 are **rejected at the filing system level** and
require a correction statement.

Architectural principle
-----------------------
This module implements a **transition-aware lookup**: the function
``get_applicable_tds_rule()`` takes a ``payment_date`` and returns
the correct section reference, rate, and threshold for the Act that was
in force on that date.  A CA firm processing March 2026 invoices (old
Act) and April 2026 invoices (new Act) **in the same week** is
completely normal during the transition period.

**Caveat:** the exact numeric CBDT payment codes (e.g. "1017" vs "1023"
for contractor payments to individuals) are stored as an updatable
lookup value, never hardcoded ground truth — they are subject to
clarification circulars.

Nothing here touches the database, HTTP, or any LLM client.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

__all__ = [
    "ACT_TRANSITION_DATE",
    "FORM_NAME_MAPPING",
    "INCOME_TAX_TDS_SEED_DATA",
    "PARTNER_PAYMENT_TDS_RULE",
    "TDSRule",
    "get_applicable_tds_rule",
]


# ---------------------------------------------------------------------------
# The transition boundary
# ---------------------------------------------------------------------------

ACT_TRANSITION_DATE: Final[date] = date(2026, 4, 1)


# ---------------------------------------------------------------------------
# Typed row shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TDSRule:
    """One row in the ``income_tax_section_map`` table.

    ``legacy_section`` is the human-readable key (e.g. "194C").
    ``new_act_reference`` is the Section 392/393 reference under the
    new Act.  ``cbdt_payment_code`` is nullable and updatable separately.
    """

    nature_of_payment: str
    legacy_section: str
    new_act_reference: str
    rate_percent: Decimal
    threshold_inr: Decimal
    threshold_notes: str = ""
    notes: str = ""
    cbdt_payment_code: str | None = None   # updatable via data migration
    effective_from: date = ACT_TRANSITION_DATE


# ---------------------------------------------------------------------------
# Seed data — Part 4.2 legacy → new Act mapping
# ---------------------------------------------------------------------------

INCOME_TAX_TDS_SEED_DATA: Final[tuple[TDSRule, ...]] = (
    TDSRule(
        nature_of_payment="Contractor payments (individual/HUF)",
        legacy_section="194C",
        new_act_reference="Section 393(1), Table Sl. No. 6(i)",
        rate_percent=Decimal("1"),
        threshold_inr=Decimal("30000"),
        threshold_notes="₹30,000 single or ₹1,00,000 aggregate in FY",
    ),
    TDSRule(
        nature_of_payment="Contractor payments (others)",
        legacy_section="194C",
        new_act_reference="Section 393(1), Table Sl. No. 6(i)",
        rate_percent=Decimal("2"),
        threshold_inr=Decimal("30000"),
        threshold_notes="₹30,000 single or ₹1,00,000 aggregate in FY",
    ),
    TDSRule(
        nature_of_payment="Professional fees",
        legacy_section="194J",
        new_act_reference="Section 393, Sl. No. 6(i)(b)-equivalent",
        rate_percent=Decimal("10"),
        threshold_inr=Decimal("30000"),
    ),
    TDSRule(
        nature_of_payment="Technical services fees",
        legacy_section="194J",
        new_act_reference="Section 393, Sl. No. 6(i)(b)-equivalent",
        rate_percent=Decimal("2"),
        threshold_inr=Decimal("30000"),
    ),
    TDSRule(
        nature_of_payment="Rent (land/building/furniture)",
        legacy_section="194I",
        new_act_reference="Section 393",
        rate_percent=Decimal("10"),
        threshold_inr=Decimal("50000"),
        threshold_notes="₹50,000/month (₹2,40,000/year aggregate convention)",
    ),
    TDSRule(
        nature_of_payment="Purchase of goods",
        legacy_section="194Q",
        new_act_reference="Section 393(1)",
        rate_percent=Decimal("0.1"),
        threshold_inr=Decimal("5000000"),
        threshold_notes=(
            "₹50,00,000 aggregate purchase from one seller, AND "
            "buyer's prior-FY turnover > ₹10 crore (both conditions required)"
        ),
    ),
    TDSRule(
        nature_of_payment="Payments to partners (remuneration/interest/bonus)",
        legacy_section="194T",
        new_act_reference="Section 393",
        rate_percent=Decimal("10"),
        threshold_inr=Decimal("20000"),
        threshold_notes="₹20,000 aggregate in FY",
        notes=(
            "New obligation from Finance Act 2024, effective 1 April 2025. "
            "20% if partner has not furnished PAN. Deposit by 7th of "
            "following month. Reporting on Form 140 (quarterly)."
        ),
    ),
    TDSRule(
        nature_of_payment="Salary",
        legacy_section="192",
        new_act_reference="Section 392",
        rate_percent=Decimal("0"),   # slab rates — 0 here means "per slab"
        threshold_inr=Decimal("0"),  # basic exemption threshold applies
        notes="Rate is per income tax slab, not a flat percentage.",
    ),
    TDSRule(
        nature_of_payment="Interest (other than securities)",
        legacy_section="194A",
        new_act_reference="Section 393",
        rate_percent=Decimal("10"),
        threshold_inr=Decimal("40000"),
        threshold_notes="₹40,000 (₹50,000 for senior citizens)",
    ),
    TDSRule(
        nature_of_payment="Non-resident payments",
        legacy_section="195",
        new_act_reference="Section 393 (cross-border table)",
        rate_percent=Decimal("0"),   # per DTAA / specified rate
        threshold_inr=Decimal("0"),  # applies from first rupee
        notes="Rate per DTAA or specified schedule. Nil threshold — applies from first rupee.",
    ),
)


# ---------------------------------------------------------------------------
# Section 194T / New Act partner payment TDS — highlighted separately
# because it's operationally critical for CA firm clients.
# ---------------------------------------------------------------------------

PARTNER_PAYMENT_TDS_RULE: Final[TDSRule] = TDSRule(
    nature_of_payment="Payments to partners (remuneration/interest/bonus)",
    legacy_section="194T",
    new_act_reference="Section 393",
    rate_percent=Decimal("10"),
    threshold_inr=Decimal("20000"),
    threshold_notes="₹20,000 aggregate per partner per FY",
    notes=(
        "Applies to all partnership firms and LLPs. 20% if partner has "
        "not furnished PAN. Deduct at time of credit or payment, whichever "
        "is earlier. Deposit by 7th of following month. Report on Form 140 "
        "(quarterly). Surface proactively in Forensic Accountant and Tax "
        "Strategist modes for any partnership/LLP client."
    ),
)


# ---------------------------------------------------------------------------
# Form name mapping — Part 4.4
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FormNameMapping:
    """Old → new form name under the Income Tax Act transition."""

    old_form: str
    new_form: str
    purpose: str


FORM_NAME_MAPPING: Final[tuple[FormNameMapping, ...]] = (
    FormNameMapping("Form 16", "Form 130", "Salary TDS certificate"),
    FormNameMapping("Form 15G / 15H", "Form 121", "Declaration for nil/lower TDS"),
    FormNameMapping(
        "Form 26AS-linked PAN TDS certificates",
        "Form 141",
        "PAN-based TDS (property, rent, VDA, individual contractor/professional payments)",
    ),
)


# ---------------------------------------------------------------------------
# Transition-aware lookup
# ---------------------------------------------------------------------------


def get_applicable_tds_rule(
    nature_of_payment: str,
    payment_date: date,
    *,
    seed_data: tuple[TDSRule, ...] = INCOME_TAX_TDS_SEED_DATA,
) -> TDSRule | None:
    """Return the correct TDS rule for a payment, based on the Act in force.

    Pre-2026-04-01 → legacy Income Tax Act 1961 section numbers (194C, etc.)
    Post-2026-04-01 → Income Tax Act 2025, Section 392/393 with payment codes.

    Rates and thresholds are pulled from the data table, NEVER hardcoded.
    Returns ``None`` when no matching rule is found.
    """
    use_legacy = payment_date < ACT_TRANSITION_DATE
    nature_lower = nature_of_payment.lower()

    for rule in seed_data:
        if nature_lower in rule.nature_of_payment.lower():
            # Return a view that uses the appropriate section reference.
            if use_legacy:
                return TDSRule(
                    nature_of_payment=rule.nature_of_payment,
                    legacy_section=rule.legacy_section,
                    new_act_reference=rule.legacy_section,   # present legacy section as the reference
                    rate_percent=rule.rate_percent,
                    threshold_inr=rule.threshold_inr,
                    threshold_notes=rule.threshold_notes,
                    notes=rule.notes,
                    cbdt_payment_code=None,   # no CBDT codes in the old Act
                    effective_from=rule.effective_from,
                )
            return rule

    return None
