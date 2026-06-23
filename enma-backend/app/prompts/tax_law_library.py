"""Codified tax-law reference modules — the single source of legal context.

Phase 5 grows this from the Phase 4 minimal subset into the full
reference the tax engine and the (eventual) supervisor LLM both consult.

Two public surfaces live here:

1. **Document-type law context** (LLM-facing). Each :data:`DOCUMENT_TYPES`
   value maps to a short statutory paragraph the extractor / classifier
   prompts can inject. Phase 5 expands these with Section 17(5), RCM
   9(3)/9(4), and Section 51 GST-TDS references.
2. **Engine-facing dispatch tables** (deterministic). The tax engine
   calls :func:`modules_for_verdict` to decide which sub-engines
   (blocked, rcm, tds, itc) are activated for a given ``document_type``,
   and looks up the structured rule tables :data:`BLOCKED_CATEGORIES`,
   :data:`RCM_TRIGGERS`, :data:`GST_TDS_THRESHOLDS`.

Scope boundary (per ADR-005 rev 2)
----------------------------------
**This library encodes GST law only.** Section 51 GST-TDS (government /
PSU deductors) IS in scope. Income-Tax Act TDS (194C, 194J, 194H, …) is
**OUT of scope for v1.** The ``tds_amount`` verdict variable carries
GST-TDS exclusively.

The strings are paraphrases of statutory text plus practitioner
shorthand — never copied from copyrighted commentary.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

__all__ = [
    "BLOCKED_CATEGORIES",
    "DOCUMENT_TYPES",
    "EngineModule",
    "GST_TDS_THRESHOLDS",
    "RCM_TRIGGERS",
    "RcmTrigger",
    "GstTdsThreshold",
    "BlockedCategory",
    "module_for_document_type",
    "modules_for_verdict",
]


# ---------------------------------------------------------------------------
# Canonical document_type enum
# ---------------------------------------------------------------------------

DOCUMENT_TYPES: Final[tuple[str, ...]] = (
    "B2B_INVOICE",
    "B2C_INVOICE",
    "FREIGHT",
    "RESTAURANT",
    "IMPORT",
    "PROFESSIONAL",
    "CAPITAL_GOODS",
    "UNKNOWN",
)


# ---------------------------------------------------------------------------
# LLM-facing per-type law context
# ---------------------------------------------------------------------------

_MODULES: Final[dict[str, str]] = {
    "B2B_INVOICE": (
        "B2B INVOICE — Section 31 tax invoice. Vendor and buyer are both "
        "registered taxpayers. ITC may be available subject to Section 16 "
        "conditions (possession of invoice, receipt of goods/services, "
        "supplier compliance via GSTR-2B). Section 16(4) bars ITC after the "
        "30 November of the FY following the invoice's FY. RCM under "
        "Section 9(4) is generally suspended for unregistered vendor "
        "supplies in v1, except notified categories."
    ),
    "B2C_INVOICE": (
        "B2C INVOICE — Sale to an unregistered person. ITC is generally "
        "not available to the recipient. The supplier may still be required "
        "to remit GST. No verdict computation applies on the recipient side."
    ),
    "FREIGHT": (
        "FREIGHT / GTA — Goods Transport Agency service. Section 9(3) "
        "attracts Reverse Charge Mechanism (RCM) on the recipient when the "
        "GTA charges 5% without ITC. RCM does NOT apply when the GTA opts "
        "for 12% forward charge. Verify by inspecting tax rate: 5% with no "
        "GST collected ⇒ RCM; 12% ⇒ forward charge ⇒ ITC eligible."
    ),
    "RESTAURANT": (
        "RESTAURANT BILL — BLOCKED CREDIT under Section 17(5)(b)(i). ITC "
        "is NOT claimable on food and beverages supplied by a restaurant, "
        "hotel, or outdoor caterer unless the input is used to make an "
        "outward taxable supply of the same line of business."
    ),
    "IMPORT": (
        "IMPORT — Goods or services imported into India. IGST is paid "
        "under reverse charge or via Customs (Bill of Entry). ITC of IGST "
        "is available subject to Section 16 — verify BoE possession."
    ),
    "PROFESSIONAL": (
        "PROFESSIONAL SERVICES — Legal, audit, consultancy, etc. Section "
        "9(3) attracts RCM specifically on legal services supplied by an "
        "advocate / firm of advocates to a business entity. Other "
        "professional services follow standard forward charge. Section 16 "
        "ITC conditions apply uniformly."
    ),
    "CAPITAL_GOODS": (
        "CAPITAL GOODS — Plant, machinery, equipment capitalised in the "
        "books. ITC is available but must be claimed in full in the month "
        "of receipt (no spreading). Common ledger pitfall: claiming as "
        "regular input instead. Section 16(3) bars depreciation on the GST "
        "component if ITC is claimed."
    ),
    "UNKNOWN": (
        "UNKNOWN DOCUMENT — Could not confidently classify. Surface the "
        "raw extraction and ask the CA to assign the correct type. No "
        "verdict computation runs on UNKNOWN documents."
    ),
}


def module_for_document_type(document_type: str) -> str:
    """Return the law-context paragraph for ``document_type``.

    Falls back to ``UNKNOWN`` if the type isn't recognised — never raises.
    """
    return _MODULES.get(document_type, _MODULES["UNKNOWN"])


# ---------------------------------------------------------------------------
# Engine-facing dispatch — which sub-engines run for which document_type
# ---------------------------------------------------------------------------


class EngineModule(StrEnum):
    """Sub-engines the tax orchestrator can activate.

    The orchestrator runs each activated module in a fixed order:
    BLOCKED → RCM → TDS → ITC. Each module's output funnels into the
    final five-variable verdict.
    """

    BLOCKED = "blocked"
    RCM = "rcm"
    TDS = "tds"
    ITC = "itc"


# Per-type dispatch table. UNKNOWN and B2C produce no verdict — the
# orchestrator short-circuits them with zeros for all five variables.
_MODULES_BY_TYPE: Final[dict[str, frozenset[EngineModule]]] = {
    "B2B_INVOICE": frozenset(
        {EngineModule.BLOCKED, EngineModule.RCM, EngineModule.TDS, EngineModule.ITC}
    ),
    "FREIGHT": frozenset({EngineModule.RCM, EngineModule.TDS, EngineModule.ITC}),
    "RESTAURANT": frozenset({EngineModule.BLOCKED}),
    "IMPORT": frozenset({EngineModule.ITC}),
    "PROFESSIONAL": frozenset({EngineModule.RCM, EngineModule.TDS, EngineModule.ITC}),
    "CAPITAL_GOODS": frozenset({EngineModule.BLOCKED, EngineModule.TDS, EngineModule.ITC}),
    "B2C_INVOICE": frozenset(),
    "UNKNOWN": frozenset(),
}


def modules_for_verdict(document_type: str) -> frozenset[EngineModule]:
    """Return the set of sub-engines that run for ``document_type``.

    Unknown / unrecognised types return an empty frozenset — the
    orchestrator treats this as "no verdict to compute" and emits a
    zeros-only verdict with reason ``unsupported_document_type``.
    """
    return _MODULES_BY_TYPE.get(document_type, frozenset())


# ---------------------------------------------------------------------------
# Section 17(5) — blocked credit categories
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlockedCategory:
    """One entry on the Section 17(5) block list.

    ``code`` is the engine-side identifier (used in ``reasons`` arrays).
    ``citation`` is the statutory anchor a CA can audit against. ``hsn_prefixes``
    is the deterministic match: any line whose HSN/SAC starts with one of
    these prefixes triggers the block, before the engine even consults
    firm rules.
    """

    code: str
    citation: str
    description: str
    hsn_prefixes: tuple[str, ...]
    document_type_match: str | None = None


# Phase 5 ships the cases the implementation programme enumerates plus
# their HSN/SAC anchors. Updated with the full Section 17(5) list per
# the Tax Knowledge Base (Part 5).
BLOCKED_CATEGORIES: Final[tuple[BlockedCategory, ...]] = (
    BlockedCategory(
        code="motor_vehicle",
        citation="Section 17(5)(a)",
        description=(
            "Motor vehicles for transport of persons with seating capacity "
            "≤13 (including driver), unless used for further supply, "
            "passenger transport service, or driving training."
        ),
        # HSN 8703 = motor cars; SAC 9966 = rental of transport vehicles.
        hsn_prefixes=("8703", "9966"),
    ),
    BlockedCategory(
        code="food_beverage",
        citation="Section 17(5)(b)(i)",
        description=(
            "Food, beverages, outdoor catering — unless the inward supply "
            "is used to make a taxable outward supply of the same category."
        ),
        # SAC 9963 covers accommodation, food, beverage services.
        hsn_prefixes=("9963",),
        document_type_match="RESTAURANT",
    ),
    BlockedCategory(
        code="beauty_health",
        citation="Section 17(5)(b)(i)",
        description=(
            "Beauty treatment, health services, cosmetic and plastic "
            "surgery — blocked unless used for outward supply of the same "
            "line."
        ),
        # SAC 9993 = human health services; 9972 = beauty treatment.
        hsn_prefixes=("9993", "9972"),
    ),
    BlockedCategory(
        code="club_membership",
        citation="Section 17(5)(b)(ii)",
        description=(
            "Membership of a club, health centre, or fitness centre — "
            "blocked unconditionally for the recipient."
        ),
        hsn_prefixes=("9995",),
    ),
    BlockedCategory(
        code="rent_a_cab",
        citation="Section 17(5)(b)(iii)",
        description=(
            "Rent-a-cab services — blocked unless the recipient is in the "
            "same line of business or required to provide such services to "
            "employees under law."
        ),
        hsn_prefixes=("9964",),
    ),
    BlockedCategory(
        code="life_health_insurance",
        citation="Section 17(5)(b)(iii)",
        description=(
            "Life and health insurance — blocked unless mandated by law "
            "for the recipient to provide to employees."
        ),
        hsn_prefixes=("9971",),
    ),
    # ── Added per Tax Knowledge Base Part 5 ─────────────────────────────
    BlockedCategory(
        code="travel_benefits",
        citation="Section 17(5)(b)(iv)",
        description=(
            "Travel benefits to employees — leave or home travel concession. "
            "ITC blocked unconditionally on the employer."
        ),
        hsn_prefixes=("9964T",),  # SAC variant for employee travel
    ),
    BlockedCategory(
        code="works_contract_construction",
        citation="Section 17(5)(c)",
        description=(
            "Works contract services for construction of an immovable "
            "property (other than plant & machinery). Exception: where it "
            "is an input service for further supply of works contract service."
        ),
        hsn_prefixes=("9954",),
    ),
    BlockedCategory(
        code="immovable_property_construction",
        citation="Section 17(5)(d)",
        description=(
            "Goods or services received for construction of an immovable "
            "property on own account (other than plant & machinery), "
            "including when used in the course of business."
        ),
        hsn_prefixes=(),  # detected by description keywords, not HSN
    ),
    BlockedCategory(
        code="personal_consumption",
        citation="Section 17(5)(g)",
        description=(
            "Goods or services used for personal consumption — ITC "
            "blocked unconditionally."
        ),
        hsn_prefixes=(),  # detected by description keywords, not HSN
    ),
    BlockedCategory(
        code="lost_stolen_destroyed",
        citation="Section 17(5)(h)",
        description=(
            "Goods lost, stolen, destroyed, written off, or disposed of "
            "as gift or free sample — ITC on such goods must be reversed."
        ),
        hsn_prefixes=(),  # detected by event/description, not HSN
    ),
)


# ---------------------------------------------------------------------------
# RCM — Section 9(3) / 9(4) triggers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RcmTrigger:
    """A condition under which the recipient self-assesses GST.

    ``citation`` anchors to the section / notification. ``hsn_prefixes``
    is the deterministic SAC match the engine uses. ``forward_charge_rate``
    is the GST percent (as :class:`Decimal`) above which the supplier is
    deemed to have opted out of RCM (GTA's 12% case).
    """

    code: str
    citation: str
    description: str
    hsn_prefixes: tuple[str, ...]
    document_type_match: str | None = None
    forward_charge_rate: Decimal | None = None


RCM_TRIGGERS: Final[tuple[RcmTrigger, ...]] = (
    RcmTrigger(
        code="gta_freight",
        citation="Section 9(3), Notification 13/2017-CT(R) entry 1",
        description=(
            "Goods Transport Agency (GTA) services — RCM applies on the "
            "recipient at 5% (without ITC for the GTA). The GTA may opt "
            "for forward charge at 12% — in which case RCM does NOT apply."
        ),
        hsn_prefixes=("9965", "9967"),
        document_type_match="FREIGHT",
        forward_charge_rate=Decimal("12"),
    ),
    RcmTrigger(
        code="legal_services",
        citation="Section 9(3), Notification 13/2017-CT(R) entry 2",
        description=(
            "Legal services supplied by an advocate or firm of advocates "
            "to a business entity — RCM applies on the recipient."
        ),
        hsn_prefixes=("9982",),
        document_type_match="PROFESSIONAL",
    ),
    RcmTrigger(
        code="sponsorship",
        citation="Section 9(3), Notification 13/2017-CT(R) entry 4",
        description=(
            "Sponsorship services supplied to a body corporate or "
            "partnership firm — RCM applies on the recipient."
        ),
        hsn_prefixes=("9983",),
    ),
    RcmTrigger(
        code="director_services",
        citation="Section 9(3), Notification 13/2017-CT(R) entry 6",
        description=(
            "Services supplied by a director to a company (other than "
            "those rendered in employee capacity) — RCM applies on the "
            "company."
        ),
        hsn_prefixes=("9983",),
    ),
    # ── Added per Tax Knowledge Base Part 6 ─────────────────────────────
    RcmTrigger(
        code="security_services",
        citation="Section 9(3), Notification 13/2017-CT(R) entry 14",
        description=(
            "Security services supplied by an unregistered or individual "
            "provider to a registered recipient — RCM applies on the "
            "recipient."
        ),
        hsn_prefixes=("9985",),
    ),
    RcmTrigger(
        code="raw_cotton_agriculturist",
        citation="Section 9(3), GST 2.0 textile sector correction",
        description=(
            "Raw cotton (HSN 5201) supplied by an agriculturist to any "
            "registered person — buyer self-assesses at 5% under RCM. "
            "This preserves the ITC chain since agriculturists are not "
            "GST-registered and cannot charge GST forward. When vendor "
            "has no GSTIN and the supply is raw cotton, route to "
            "rcm_liability computation rather than treating the absent "
            "vendor GSTIN as a data-quality error."
        ),
        hsn_prefixes=("5201",),
    ),
)


# ---------------------------------------------------------------------------
# Section 51 GST-TDS — the only TDS encoded in v1
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GstTdsThreshold:
    """Section 51 GST-TDS applicability + rate.

    GST-TDS applies only when the *recipient* is a notified deductor
    (government / PSU). The recipient flag lives on ``clients.gst_tds_deductor``
    (boolean); the engine consults that flag plus this threshold.
    """

    citation: str
    threshold_inr: Decimal
    rate_percent: Decimal


GST_TDS_THRESHOLDS: Final[GstTdsThreshold] = GstTdsThreshold(
    citation="Section 51, CGST Act",
    # ₹2.5 lakh — single contract value threshold.
    threshold_inr=Decimal("250000"),
    # 2% (1% CGST + 1% SGST or 2% IGST).
    rate_percent=Decimal("2"),
)
