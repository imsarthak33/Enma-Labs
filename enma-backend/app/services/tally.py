"""Tally Prime ENVELOPE XML composer — purchase voucher export.

Background
----------
Indian SMB CAs end the GST filing cycle in Tally Prime. After the
filing is approved in Enma, the CA needs to push the same vouchers
into Tally so the accounting trial-balance reflects reality. Without
this step the CA re-keys every invoice by hand — wiping out most of
Enma's productivity win.

This module composes the Tally import format documented at
https://help.tallysolutions.com/article/Tally.ERP9/Data_Migration/Import_Data/import_xml_file.htm
(`<ENVELOPE>` → `<BODY>` → `<IMPORTDATA>` → ``REPORTNAME=Vouchers``).

What we emit
------------
Each ``Document`` in the export becomes one ``<VOUCHER VCHTYPE="Purchase">``.
Inside the voucher, ledger entries follow Tally's signed-amount convention:

  * Party ledger (vendor): AMOUNT = +grand_total, ISDEEMEDPOSITIVE=No
    (i.e. credit — money owed to the vendor).
  * One Purchase ledger per per-rate slice: AMOUNT = -taxable_for_that_rate,
    ISDEEMEDPOSITIVE=Yes (debit — the expense). Ledger name encodes
    the rate, e.g. ``Purchase @ 18%``, so the CA's chart-of-accounts
    can hold separate input-credit buckets per slab.
  * Input GST ledgers per per-rate slice. Intra-state: ``Input CGST @ X%``
    + ``Input SGST @ X%`` (each half the slab). Inter-state:
    ``Input IGST @ Y%`` (the full slab).

Amounts sum to zero per voucher — that's the Tally invariant.

Ledger naming
-------------
We use canonical Indian conventions ("Purchase @ 18%", "Input CGST @
9%"). The CA's Tally company must have these ledgers configured under
those exact names for the import to land cleanly. Reading the CA's
actual chart of accounts via Tally ODBC is post-MVP.

Determinism
-----------
The composer is a pure function. Given the same inputs, byte-identical
output. Vouchers are emitted in the input order; the SHA-256 of the
output is recorded on the ``tally_export_runs`` audit row so a CA
re-running the same period can be told "this is the same file you
got on Tuesday" instead of getting a silently different file.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final
from xml.sax.saxutils import escape

from app.tax.reconciler import ReconciledInvoice
from app.utils.decimal_utils import ZERO

__all__ = [
    "TallyInvoice",
    "compose_tally_envelope",
]


# ---------------------------------------------------------------------------
# Input shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TallyInvoice:
    """One source invoice ready for Tally voucher emission.

    All fields come from the document's extraction + reconciliation —
    the composer never touches the database or the Telegram API.

    ``vendor_name`` is the ledger name used both for the party-ledger
    reference and the first ``<ALLLEDGERENTRIES.LIST>`` row. The
    composer XML-escapes it; callers pass it raw.

    ``invoice_grand_total`` is the figure printed on the invoice's
    "Grand Total" line. When provided and it differs from
    ``reconciled.grand_total`` (typically by 1 paisa due to per-line
    quantisation), the composer emits a ``Rounded Off`` ledger entry
    so the party ledger reflects the actual payable. Omit to use the
    reconciler's canonical total verbatim.
    """

    vendor_name: str
    invoice_number: str
    invoice_date: date
    reconciled: ReconciledInvoice
    invoice_grand_total: Decimal | None = None


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


_NEWLINE: Final[str] = "\n"


def _format_amount(value: Decimal) -> str:
    """Render a Decimal as Tally-style two-decimal text (e.g. ``-232.14``)."""
    return f"{value:.2f}"


def _format_rate(rate: Decimal) -> str:
    """Render a tax rate without trailing zeros (``5``, ``2.5``, ``9``).

    Tally ledger names traditionally read ``Purchase @ 5%`` not
    ``Purchase @ 5.00%``. ``Decimal.normalize()`` strips trailing
    zeros, but it can produce scientific notation for whole numbers
    (``Decimal('5').normalize() == Decimal('5')`` is fine; this guard
    handles future-proofing if rate ever carries an exponent).
    """
    if rate == ZERO:
        return "0"
    normalized = rate.normalize()
    text = format(normalized, "f")
    # ``format(Decimal('5'), 'f')`` → "5"; ``format(Decimal('2.5'), 'f')`` → "2.5".
    return text


def _format_date(d: date) -> str:
    """Tally wants ``YYYYMMDD``."""
    return d.strftime("%Y%m%d")


def _xml_escape(text: str) -> str:
    """XML-safe escape — covers ``&``, ``<``, ``>``."""
    return escape(text)


# ---------------------------------------------------------------------------
# Ledger naming
# ---------------------------------------------------------------------------


def _purchase_ledger_name(rate: Decimal) -> str:
    return f"Purchase @ {_format_rate(rate)}%"


def _input_cgst_ledger_name(half_rate: Decimal) -> str:
    return f"Input CGST @ {_format_rate(half_rate)}%"


def _input_sgst_ledger_name(half_rate: Decimal) -> str:
    return f"Input SGST @ {_format_rate(half_rate)}%"


def _input_igst_ledger_name(rate: Decimal) -> str:
    return f"Input IGST @ {_format_rate(rate)}%"


# ---------------------------------------------------------------------------
# Per-voucher emission
# ---------------------------------------------------------------------------


def _emit_ledger_entry(
    *,
    ledger_name: str,
    amount: Decimal,
    is_party: bool,
) -> list[str]:
    """Render one ``<ALLLEDGERENTRIES.LIST>`` block.

    ``is_party=True`` flags the party ledger (the vendor). Tally
    uses ``ISDEEMEDPOSITIVE=No`` for the party entry (a credit) and
    ``Yes`` for the counter-side entries (debits to expense / input-tax
    ledgers).
    """
    deemed_positive = "No" if is_party else "Yes"
    is_party_text = "Yes" if is_party else "No"
    return [
        "      <ALLLEDGERENTRIES.LIST>",
        f"        <LEDGERNAME>{_xml_escape(ledger_name)}</LEDGERNAME>",
        f"        <ISDEEMEDPOSITIVE>{deemed_positive}</ISDEEMEDPOSITIVE>",
        f"        <ISPARTYLEDGER>{is_party_text}</ISPARTYLEDGER>",
        f"        <AMOUNT>{_format_amount(amount)}</AMOUNT>",
        "      </ALLLEDGERENTRIES.LIST>",
    ]


def _emit_voucher(inv: TallyInvoice) -> list[str]:
    """Render one ``<VOUCHER>`` block as a list of indented XML lines."""
    rec = inv.reconciled
    vendor = _xml_escape(inv.vendor_name)
    narration = _xml_escape(f"Invoice {inv.invoice_number} from {inv.vendor_name}")

    # Party ledger amount: prefer the invoice's printed grand total when
    # supplied (matches the CA's books exactly). Difference is reconciled
    # by a "Rounded Off" entry below.
    if inv.invoice_grand_total is not None:
        party_amount = inv.invoice_grand_total
        rounding_diff = party_amount - rec.grand_total
    else:
        party_amount = rec.grand_total
        rounding_diff = ZERO

    lines: list[str] = [
        '    <VOUCHER VCHTYPE="Purchase" ACTION="Create">',
        f"      <DATE>{_format_date(inv.invoice_date)}</DATE>",
        f"      <NARRATION>{narration}</NARRATION>",
        "      <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>",
        f"      <VOUCHERNUMBER>{_xml_escape(inv.invoice_number)}</VOUCHERNUMBER>",
        f"      <PARTYLEDGERNAME>{vendor}</PARTYLEDGERNAME>",
    ]

    # 1. Party ledger entry — full grand total, credit.
    lines.extend(
        _emit_ledger_entry(
            ledger_name=inv.vendor_name,
            amount=party_amount,
            is_party=True,
        )
    )

    # 2. Per-rate slices.
    if rec.rate_breakdown:
        # Group taxable by tax_rate so we can emit one Purchase @ X%
        # ledger per rate slice. Per-rate CGST/SGST/IGST already arrive
        # bucketed in rec.rate_breakdown.
        taxable_by_rate: dict[Decimal, Decimal] = {}
        for li in rec.line_items:
            taxable_by_rate[li.tax_rate] = taxable_by_rate.get(li.tax_rate, ZERO) + li.taxable

        for rate, cgst, sgst, igst in rec.rate_breakdown:
            taxable_for_rate = taxable_by_rate.get(rate, ZERO)
            lines.extend(
                _emit_ledger_entry(
                    ledger_name=_purchase_ledger_name(rate),
                    amount=-taxable_for_rate,
                    is_party=False,
                )
            )
            if rec.intra_state:
                half_rate = rate / Decimal(2)
                if cgst != ZERO:
                    lines.extend(
                        _emit_ledger_entry(
                            ledger_name=_input_cgst_ledger_name(half_rate),
                            amount=-cgst,
                            is_party=False,
                        )
                    )
                if sgst != ZERO:
                    lines.extend(
                        _emit_ledger_entry(
                            ledger_name=_input_sgst_ledger_name(half_rate),
                            amount=-sgst,
                            is_party=False,
                        )
                    )
            elif igst != ZERO:
                lines.extend(
                    _emit_ledger_entry(
                        ledger_name=_input_igst_ledger_name(rate),
                        amount=-igst,
                        is_party=False,
                    )
                )
    else:
        # No line items reconciled — emit a single Purchase @ 0% line
        # with the full taxable as a debit, so the voucher still balances.
        lines.extend(
            _emit_ledger_entry(
                ledger_name=_purchase_ledger_name(ZERO),
                amount=-rec.taxable,
                is_party=False,
            )
        )

    # 3. Rounded Off entry — when the invoice's printed total disagrees
    # with our canonical math by a paisa (or two) of per-line quantisation,
    # absorb the difference here so the voucher balances to zero.
    if rounding_diff != ZERO:
        lines.extend(
            _emit_ledger_entry(
                ledger_name="Rounded Off",
                amount=-rounding_diff,
                is_party=False,
            )
        )

    lines.append("    </VOUCHER>")
    return lines


# ---------------------------------------------------------------------------
# Top-level composer
# ---------------------------------------------------------------------------


def compose_tally_envelope(
    *,
    company_name: str,
    invoices: list[TallyInvoice] | tuple[TallyInvoice, ...],
) -> bytes:
    """Return the full ENVELOPE document as UTF-8 bytes.

    ``company_name`` is the Tally company the import targets — by
    convention the client's trade_name. The CA opens the matching
    company in Tally before importing.

    Vouchers are emitted in the supplied order. Pass invoices already
    sorted by ``invoice_date`` (or any deterministic key) if you want
    byte-stable output across runs.
    """
    body: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<ENVELOPE>",
        "  <HEADER>",
        "    <TALLYREQUEST>Import Data</TALLYREQUEST>",
        "  </HEADER>",
        "  <BODY>",
        "    <IMPORTDATA>",
        "      <REQUESTDESC>",
        "        <REPORTNAME>Vouchers</REPORTNAME>",
        "        <STATICVARIABLES>",
        f"          <SVCURRENTCOMPANY>{_xml_escape(company_name)}</SVCURRENTCOMPANY>",
        "        </STATICVARIABLES>",
        "      </REQUESTDESC>",
        "      <REQUESTDATA>",
        '  <TALLYMESSAGE xmlns:UDF="TallyUDF">',
    ]
    for inv in invoices:
        body.extend(_emit_voucher(inv))
    body.extend(
        [
            "  </TALLYMESSAGE>",
            "      </REQUESTDATA>",
            "    </IMPORTDATA>",
            "  </BODY>",
            "</ENVELOPE>",
        ]
    )
    return _NEWLINE.join(body).encode("utf-8")
