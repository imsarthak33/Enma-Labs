"""Tally XML composer tests — golden-file assertions.

The composer is a pure function so we lock its output byte-for-byte.
That gives us:

  * A trip-wire for accidental format drift (a stray attribute, an
    extra newline, a different ledger-name convention) — the test
    file shows EXACTLY what hits the CA's Tally import.
  * SHA-256 stability — the audit row in ``tally_export_runs`` records
    the file hash, so identical re-runs produce an identical row.

The lead fixture is the SUDHA invoice used by
``tests/test_tax/test_reconciler.py``, so a regression in either
module is visible end-to-end.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal

from app.services.tally import TallyInvoice, compose_tally_envelope
from app.tax.reconciler import reconcile_extraction

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _sudha_extraction() -> dict[str, object]:
    """Same shape used by test_reconciler. Intra-state, single 5% rate."""
    return {
        "vendor": {"name": "CLEIND PRODUCT & SERVICE", "gstin": "10DYKPR4180P1ZV"},
        "buyer": {"name": "SUDHA ENTERPRISES", "gstin": "10AMEPL5872B1ZI"},
        "invoice_number": "120",
        "invoice_date": "2025-08-06",
        "line_items": [
            {
                "description": "BROOM BIG",
                "hsn_sac": "9603",
                "quantity": "150",
                "unit_price": "61.9",
                "tax_amount": "464.29",
                "line_amount": "9750",
            }
        ],
        "observed_totals": [
            {"label": "Taxable Amount", "amount": "9285.71"},
            {"label": "CGST @2.5%", "amount": "232.14"},
            {"label": "SGST @2.5%", "amount": "232.14"},
            {"label": "Total Amount", "amount": "9750"},
        ],
    }


def _multi_rate_extraction() -> dict[str, object]:
    """Two line items at 5% and 18% — intra-state. Exercises per-rate slicing."""
    return {
        "vendor": {"name": "ACME TRADERS", "gstin": "27ABCDE1234F1Z5"},
        "buyer": {"name": "BUYER CO", "gstin": "27ZZZZZ9999Z1Z9"},
        "invoice_number": "INV-2025-077",
        "invoice_date": "2025-07-15",
        "line_items": [
            # 5% line — taxable 1000, tax 50, total 1050
            {
                "description": "Item A",
                "quantity": "10",
                "unit_price": "100",
                "tax_amount": "50",
                "line_amount": "1050",
            },
            # 18% line — taxable 2000, tax 360, total 2360
            {
                "description": "Item B",
                "quantity": "10",
                "unit_price": "200",
                "tax_amount": "360",
                "line_amount": "2360",
            },
        ],
        "observed_totals": [],
    }


def _interstate_extraction() -> dict[str, object]:
    """Vendor in state 27, buyer in 10 — inter-state IGST."""
    return {
        "vendor": {"name": "MAHARASHTRA SUPPLY", "gstin": "27ABCDE1234F1Z5"},
        "buyer": {"name": "BIHAR BUYER", "gstin": "10ZZZZZ9999Z1Z9"},
        "invoice_number": "MS-001",
        "invoice_date": "2025-07-20",
        "line_items": [
            # 18% line — taxable 1000, tax 180, total 1180
            {
                "description": "Inter-state item",
                "quantity": "10",
                "unit_price": "100",
                "tax_amount": "180",
                "line_amount": "1180",
            }
        ],
        "observed_totals": [],
    }


# ---------------------------------------------------------------------------
# Golden output for the SUDHA single-rate intra-state case
# ---------------------------------------------------------------------------


_SUDHA_EXPECTED = b"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
        <STATICVARIABLES>
          <SVCURRENTCOMPANY>SUDHA ENTERPRISES</SVCURRENTCOMPANY>
        </STATICVARIABLES>
      </REQUESTDESC>
      <REQUESTDATA>
  <TALLYMESSAGE xmlns:UDF="TallyUDF">
    <VOUCHER VCHTYPE="Purchase" ACTION="Create">
      <DATE>20250806</DATE>
      <NARRATION>Invoice 120 from CLEIND PRODUCT &amp; SERVICE</NARRATION>
      <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
      <VOUCHERNUMBER>120</VOUCHERNUMBER>
      <PARTYLEDGERNAME>CLEIND PRODUCT &amp; SERVICE</PARTYLEDGERNAME>
      <ALLLEDGERENTRIES.LIST>
        <LEDGERNAME>CLEIND PRODUCT &amp; SERVICE</LEDGERNAME>
        <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
        <ISPARTYLEDGER>Yes</ISPARTYLEDGER>
        <AMOUNT>9750.00</AMOUNT>
      </ALLLEDGERENTRIES.LIST>
      <ALLLEDGERENTRIES.LIST>
        <LEDGERNAME>Purchase @ 5%</LEDGERNAME>
        <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
        <ISPARTYLEDGER>No</ISPARTYLEDGER>
        <AMOUNT>-9285.71</AMOUNT>
      </ALLLEDGERENTRIES.LIST>
      <ALLLEDGERENTRIES.LIST>
        <LEDGERNAME>Input CGST @ 2.5%</LEDGERNAME>
        <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
        <ISPARTYLEDGER>No</ISPARTYLEDGER>
        <AMOUNT>-232.14</AMOUNT>
      </ALLLEDGERENTRIES.LIST>
      <ALLLEDGERENTRIES.LIST>
        <LEDGERNAME>Input SGST @ 2.5%</LEDGERNAME>
        <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
        <ISPARTYLEDGER>No</ISPARTYLEDGER>
        <AMOUNT>-232.14</AMOUNT>
      </ALLLEDGERENTRIES.LIST>
      <ALLLEDGERENTRIES.LIST>
        <LEDGERNAME>Rounded Off</LEDGERNAME>
        <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
        <ISPARTYLEDGER>No</ISPARTYLEDGER>
        <AMOUNT>-0.01</AMOUNT>
      </ALLLEDGERENTRIES.LIST>
    </VOUCHER>
  </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sudha_invoice_golden_file() -> None:
    """End-to-end: extraction → reconciler → composer == golden bytes.

    Note the canonical-math rounding: per-line CGST/SGST each quantise
    to 232.14 (from 232.14275), so the reconciler's grand_total is
    9749.99 — one paisa short of the invoice's printed 9750.00. The
    composer absorbs that into a ``Rounded Off`` ledger entry when
    ``invoice_grand_total`` is supplied, matching the Indian CA
    convention.
    """
    reconciled = reconcile_extraction(_sudha_extraction())
    inv = TallyInvoice(
        vendor_name="CLEIND PRODUCT & SERVICE",
        invoice_number="120",
        invoice_date=date(2025, 8, 6),
        reconciled=reconciled,
        invoice_grand_total=Decimal("9750.00"),
    )
    out = compose_tally_envelope(company_name="SUDHA ENTERPRISES", invoices=[inv])
    assert out == _SUDHA_EXPECTED


def test_composer_is_deterministic() -> None:
    """Same input → same bytes → same SHA-256. Audit-row stability invariant."""
    reconciled = reconcile_extraction(_sudha_extraction())
    inv = TallyInvoice(
        vendor_name="CLEIND PRODUCT & SERVICE",
        invoice_number="120",
        invoice_date=date(2025, 8, 6),
        reconciled=reconciled,
        invoice_grand_total=Decimal("9750.00"),
    )
    a = compose_tally_envelope(company_name="SUDHA ENTERPRISES", invoices=[inv])
    b = compose_tally_envelope(company_name="SUDHA ENTERPRISES", invoices=[inv])
    assert a == b
    assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()


def test_multi_rate_intra_state_emits_one_purchase_per_slice() -> None:
    """5% and 18% line items → two Purchase ledgers, four Input GST ledgers."""
    reconciled = reconcile_extraction(_multi_rate_extraction())
    inv = TallyInvoice(
        vendor_name="ACME TRADERS",
        invoice_number="INV-2025-077",
        invoice_date=date(2025, 7, 15),
        reconciled=reconciled,
    )
    out = compose_tally_envelope(company_name="BUYER CO", invoices=[inv]).decode()
    # Two Purchase ledgers — one per slab.
    assert "<LEDGERNAME>Purchase @ 5%</LEDGERNAME>" in out
    assert "<LEDGERNAME>Purchase @ 18%</LEDGERNAME>" in out
    # Intra-state: CGST + SGST at half-rate for each slab.
    assert "<LEDGERNAME>Input CGST @ 2.5%</LEDGERNAME>" in out
    assert "<LEDGERNAME>Input SGST @ 2.5%</LEDGERNAME>" in out
    assert "<LEDGERNAME>Input CGST @ 9%</LEDGERNAME>" in out
    assert "<LEDGERNAME>Input SGST @ 9%</LEDGERNAME>" in out
    # No IGST — we're intra-state.
    assert "Input IGST" not in out
    # The taxable for each slab matches the line item we created.
    assert "<AMOUNT>-1000.00</AMOUNT>" in out  # 5% slab purchase
    assert "<AMOUNT>-2000.00</AMOUNT>" in out  # 18% slab purchase


def test_interstate_emits_igst_only() -> None:
    """Vendor state 27, buyer 10 → IGST, no CGST/SGST."""
    reconciled = reconcile_extraction(_interstate_extraction())
    inv = TallyInvoice(
        vendor_name="MAHARASHTRA SUPPLY",
        invoice_number="MS-001",
        invoice_date=date(2025, 7, 20),
        reconciled=reconciled,
    )
    out = compose_tally_envelope(company_name="BIHAR BUYER", invoices=[inv]).decode()
    assert "<LEDGERNAME>Input IGST @ 18%</LEDGERNAME>" in out
    assert "<AMOUNT>-180.00</AMOUNT>" in out
    assert "Input CGST" not in out
    assert "Input SGST" not in out


def test_voucher_balances_to_zero() -> None:
    """Tally invariant: AMOUNT entries on a voucher must sum to zero."""
    reconciled = reconcile_extraction(_sudha_extraction())
    inv = TallyInvoice(
        vendor_name="CLEIND PRODUCT & SERVICE",
        invoice_number="120",
        invoice_date=date(2025, 8, 6),
        reconciled=reconciled,
    )
    out = compose_tally_envelope(company_name="SUDHA ENTERPRISES", invoices=[inv]).decode()
    # Extract every AMOUNT figure and ensure they net to zero.
    import re

    amounts = [Decimal(m) for m in re.findall(r"<AMOUNT>(-?\d+\.\d{2})</AMOUNT>", out)]
    assert sum(amounts) == Decimal("0.00")


def test_empty_invoice_list_produces_envelope_skeleton() -> None:
    """Zero invoices should still produce a valid (empty-body) envelope."""
    out = compose_tally_envelope(company_name="EMPTY CO", invoices=[]).decode()
    assert "<ENVELOPE>" in out
    assert "</ENVELOPE>" in out
    assert "<VOUCHER" not in out


def test_xml_escape_in_vendor_name() -> None:
    """Vendor names with ``&``, ``<``, ``>`` must be escaped."""
    reconciled = reconcile_extraction(_sudha_extraction())
    inv = TallyInvoice(
        vendor_name="A & B <Trading>",
        invoice_number="X<1>",
        invoice_date=date(2025, 1, 1),
        reconciled=reconciled,
    )
    out = compose_tally_envelope(company_name="C&D", invoices=[inv]).decode()
    assert "A &amp; B &lt;Trading&gt;" in out
    assert "X&lt;1&gt;" in out
    assert "C&amp;D" in out
    # And the raw chars must NOT appear unescaped anywhere except inside tag syntax.
    # (A naive '<' search would match real XML tags, so we restrict to known-bad sequences.)
    assert "A & B" not in out
    assert "C&D" not in out


def test_zero_rated_invoice_emits_purchase_at_zero() -> None:
    """An invoice with no line items reconciled falls back to Purchase @ 0%."""
    # Construct a synthetic reconciled invoice with empty line_items + a taxable.
    from app.tax.reconciler import ReconciledInvoice
    from app.utils.decimal_utils import ZERO as Z

    rec = ReconciledInvoice(
        line_items=(),
        taxable=Decimal("500.00"),
        cgst_rate=Z,
        cgst_amount=Z,
        sgst_rate=Z,
        sgst_amount=Z,
        igst_rate=Z,
        igst_amount=Z,
        grand_total=Decimal("500.00"),
        rate_breakdown=(),
        intra_state=True,
        discrepancies=(),
    )
    inv = TallyInvoice(
        vendor_name="EXEMPT VENDOR",
        invoice_number="E-1",
        invoice_date=date(2025, 1, 1),
        reconciled=rec,
    )
    out = compose_tally_envelope(company_name="BUYER", invoices=[inv]).decode()
    assert "<LEDGERNAME>Purchase @ 0%</LEDGERNAME>" in out
    assert "<AMOUNT>500.00</AMOUNT>" in out      # party (credit)
    assert "<AMOUNT>-500.00</AMOUNT>" in out     # purchase (debit)


def test_multiple_vouchers_emit_in_order() -> None:
    """Pass two invoices, expect two VOUCHER blocks in input order."""
    rec_a = reconcile_extraction(_sudha_extraction())
    rec_b = reconcile_extraction(_interstate_extraction())
    inv_a = TallyInvoice(
        vendor_name="CLEIND PRODUCT & SERVICE",
        invoice_number="120",
        invoice_date=date(2025, 8, 6),
        reconciled=rec_a,
    )
    inv_b = TallyInvoice(
        vendor_name="MAHARASHTRA SUPPLY",
        invoice_number="MS-001",
        invoice_date=date(2025, 7, 20),
        reconciled=rec_b,
    )
    out = compose_tally_envelope(company_name="BUYER", invoices=[inv_a, inv_b]).decode()
    pos_a = out.find("CLEIND")
    pos_b = out.find("MAHARASHTRA")
    assert 0 <= pos_a < pos_b
    # Two vouchers.
    assert out.count('<VOUCHER VCHTYPE="Purchase"') == 2
