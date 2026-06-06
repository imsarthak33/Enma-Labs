"""Tests for the Reverse Charge Mechanism engine."""

from __future__ import annotations

from decimal import Decimal

from app.tax.rcm import compute_rcm, line_matches_trigger
from app.tax.types import LineItem, Party, Transaction


def _line(
    hsn: str | None,
    *,
    taxable: str = "10000",
    cgst: str = "0",
    sgst: str = "0",
    igst: str = "0",
) -> LineItem:
    return LineItem(
        description=None,
        hsn_sac=hsn,
        taxable_value=Decimal(taxable),
        cgst_amount=Decimal(cgst),
        sgst_amount=Decimal(sgst),
        igst_amount=Decimal(igst),
    )


def _tx(document_type: str, *lines: LineItem) -> Transaction:
    return Transaction(
        document_type=document_type,
        vendor=Party(gstin=None),
        buyer=Party(gstin=None),
        invoice_date=None,
        line_items=lines,
    )


class TestLineMatchesTrigger:
    def test_freight_document_type(self) -> None:
        trig = line_matches_trigger(_line(hsn=None), "FREIGHT")
        assert trig is not None
        assert trig.code == "gta_freight"

    def test_legal_services_hsn(self) -> None:
        trig = line_matches_trigger(_line(hsn="9982"), "B2B_INVOICE")
        assert trig is not None
        assert trig.code == "legal_services"

    def test_no_match(self) -> None:
        assert line_matches_trigger(_line(hsn="8517"), "B2B_INVOICE") is None


class TestComputeRcm:
    def test_gta_5pct_triggers_rcm_on_collected_amount(self) -> None:
        # GTA invoice at 5% intra-state: 250 + 250 = 500 on 10000.
        tx = _tx("FREIGHT", _line(hsn=None, cgst="250", sgst="250"))
        result = compute_rcm(tx)
        assert result.rcm_liability == Decimal("500.00")
        assert result.reasons == ("gta_freight",)

    def test_gta_12pct_forward_charge_disables_rcm(self) -> None:
        # 12% forward charge: 600 CGST + 600 SGST on 10000 → effective rate 12%.
        tx = _tx("FREIGHT", _line(hsn=None, cgst="600", sgst="600"))
        result = compute_rcm(tx)
        assert result.rcm_liability == Decimal("0.00")
        assert result.reasons == ()

    def test_gta_no_tax_collected_applies_default_5pct(self) -> None:
        # True RCM invoice — supplier collects nothing. 5% of 10000 = 500.
        tx = _tx("FREIGHT", _line(hsn=None, taxable="10000"))
        result = compute_rcm(tx)
        assert result.rcm_liability == Decimal("500.00")

    def test_legal_services_rcm(self) -> None:
        # Legal services have no forward-charge carve-out — RCM always
        # applies. Recipient owes the GST that should have been on the
        # invoice.
        tx = _tx(
            "B2B_INVOICE",
            _line(hsn="9982", taxable="50000", cgst="4500", sgst="4500"),
        )
        result = compute_rcm(tx)
        assert result.rcm_liability == Decimal("9000.00")
        assert result.reasons == ("legal_services",)

    def test_non_trigger_line_yields_zero(self) -> None:
        tx = _tx("B2B_INVOICE", _line(hsn="8517", cgst="90", sgst="90"))
        result = compute_rcm(tx)
        assert result.rcm_liability == Decimal("0.00")
        assert result.reasons == ()

    def test_paise_precision(self) -> None:
        # 0.5% of 10000 = 50.00 — confirm no float drift.
        tx = _tx("FREIGHT", _line(hsn=None, taxable="9999.99"))
        result = compute_rcm(tx)
        # 9999.99 * 5 / 100 = 499.9995 → quantises to 500.00 (HALF_UP).
        assert result.rcm_liability == Decimal("500.00")
