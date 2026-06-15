"""Tests for the client-ledger CSV exporter.

The composer is a pure function so we hit it directly with synthetic
``Document``-shaped objects. Assertions cover:

* The CSV starts with a UTF-8 BOM so Excel renders ₹ correctly.
* Header columns match :data:`LEDGER_HEADERS` byte-for-byte.
* The summary row math is computed by Python over reconciled totals,
  never by trusting the extractor's ``totals`` block.
* ITC status is derived correctly from the five-variable tax_verdict.
* Sale vs Purchase classification follows the client's GSTIN.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

from app.services.export import LEDGER_HEADERS, _compose_ledger_csv


def _make_doc(
    *,
    extraction: dict[str, object],
    tax_verdict: dict[str, object] | None = None,
    processing_status: str = "completed",
    created_at: datetime | None = None,
) -> MagicMock:
    """Build a Document-shaped mock the composer can read.

    The composer only ever reads four attributes off Document:
    ``extraction_data``, ``tax_verdict``, ``processing_status``,
    and ``created_at``. We mock those, full stop.
    """
    m = MagicMock()
    m.extraction_data = extraction
    m.tax_verdict = tax_verdict
    m.processing_status = processing_status
    m.created_at = created_at or datetime(2026, 6, 14, 9, 30, 0, tzinfo=UTC)
    return m


def _parse_csv(blob: bytes) -> list[list[str]]:
    """Decode the BOM-prefixed CSV into a list of rows for assertions."""
    text = blob.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _intra_state_purchase() -> dict[str, object]:
    """SUDHA-style 5% intra-state invoice from CLEIND."""
    return {
        "vendor": {"name": "CLEIND PRODUCT & SERVICE", "gstin": "10DYKPR4180P1ZV"},
        "buyer": {"name": "SUDHA ENTERPRISES", "gstin": "10AMEPL5872B1ZI"},
        "invoice_number": "120",
        "invoice_date": "2025-08-06",
        "line_items": [
            {
                "description": "BROOM BIG",
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


def _inter_state_purchase() -> dict[str, object]:
    """18% inter-state invoice (vendor in 27, buyer in 10)."""
    return {
        "vendor": {"name": "MAHARASHTRA SUPPLY", "gstin": "27ABCDE1234F1Z5"},
        "buyer": {"name": "SUDHA ENTERPRISES", "gstin": "10AMEPL5872B1ZI"},
        "invoice_number": "MS-001",
        "invoice_date": "2025-07-15",
        "line_items": [
            {
                "description": "Inter-state item",
                "quantity": "10",
                "unit_price": "100",
                "tax_amount": "180",
                "line_amount": "1180",
            }
        ],
    }


# ---------------------------------------------------------------------------
# BOM + header tests
# ---------------------------------------------------------------------------


def test_csv_starts_with_utf8_bom() -> None:
    """Excel needs the BOM to render ₹ correctly."""
    blob = _compose_ledger_csv(documents=[], client_gstin=None)
    assert blob.startswith(b"\xef\xbb\xbf")


def test_header_row_matches_constant() -> None:
    blob = _compose_ledger_csv(documents=[], client_gstin=None)
    rows = _parse_csv(blob)
    assert tuple(rows[0]) == LEDGER_HEADERS


def test_rupee_symbol_in_headers() -> None:
    """₹ MUST survive the round-trip through utf-8-sig."""
    blob = _compose_ledger_csv(documents=[], client_gstin=None)
    decoded = blob.decode("utf-8-sig")
    assert "₹" in decoded


def test_empty_documents_emits_header_plus_summary() -> None:
    blob = _compose_ledger_csv(documents=[], client_gstin=None)
    rows = _parse_csv(blob)
    # Header + summary row only.
    assert len(rows) == 2
    assert rows[1][0] == "SUMMARY"
    assert rows[1][1] == "0 invoice(s)"


# ---------------------------------------------------------------------------
# Per-row math tests
# ---------------------------------------------------------------------------


def test_intra_state_row_uses_reconciler_totals() -> None:
    """SUDHA invoice: canonical math = taxable 9285.71, CGST/SGST 232.14 each."""
    doc = _make_doc(extraction=_intra_state_purchase())
    blob = _compose_ledger_csv(documents=[doc], client_gstin="10AMEPL5872B1ZI")
    rows = _parse_csv(blob)
    # Header, body, summary.
    assert len(rows) == 3
    body = rows[1]
    assert body[0] == "06-Aug-2025"     # invoice date
    assert body[1] == "120"              # invoice no
    assert body[2] == "CLEIND PRODUCT & SERVICE"
    assert body[3] == "SUDHA ENTERPRISES"
    assert body[4] == "9285.71"          # taxable
    assert body[5] == "232.14"           # CGST
    assert body[6] == "232.14"           # SGST
    assert body[7] == "0.00"             # IGST
    assert body[8] == "9749.99"          # canonical grand total
    assert body[10] == "PURCHASE"        # client is the buyer


def test_summary_row_aggregates_totals() -> None:
    docs = [
        _make_doc(extraction=_intra_state_purchase()),
        _make_doc(
            extraction=_inter_state_purchase(),
            created_at=datetime(2026, 6, 15, 10, 0, 0, tzinfo=UTC),
        ),
    ]
    blob = _compose_ledger_csv(documents=docs, client_gstin="10AMEPL5872B1ZI")
    rows = _parse_csv(blob)
    # Header + 2 body + summary.
    assert len(rows) == 4
    summary = rows[3]
    assert summary[0] == "SUMMARY"
    assert summary[1] == "2 invoice(s)"
    # taxable = 9285.71 + 1000 = 10285.71
    assert Decimal(summary[4]) == Decimal("10285.71")
    # CGST = 232.14 (intra-state) + 0 = 232.14
    assert Decimal(summary[5]) == Decimal("232.14")
    # SGST = 232.14 + 0 = 232.14
    assert Decimal(summary[6]) == Decimal("232.14")
    # IGST = 0 + 180 = 180
    assert Decimal(summary[7]) == Decimal("180.00")
    # Grand = 9749.99 + 1180 = 10929.99
    assert Decimal(summary[8]) == Decimal("10929.99")


# ---------------------------------------------------------------------------
# ITC status derivation tests
# ---------------------------------------------------------------------------


def test_itc_status_eligible_when_claim_dominates() -> None:
    doc = _make_doc(
        extraction=_intra_state_purchase(),
        tax_verdict={"claim_amount": "232.14", "block_amount": "0",
                     "defer_amount": "0", "rcm_liability": "0"},
    )
    rows = _parse_csv(_compose_ledger_csv(documents=[doc], client_gstin=None))
    assert rows[1][9] == "ELIGIBLE"


def test_itc_status_blocked_when_block_dominates() -> None:
    doc = _make_doc(
        extraction=_intra_state_purchase(),
        tax_verdict={"claim_amount": "0", "block_amount": "232.14",
                     "defer_amount": "0", "rcm_liability": "0"},
    )
    rows = _parse_csv(_compose_ledger_csv(documents=[doc], client_gstin=None))
    assert rows[1][9] == "BLOCKED"


def test_itc_status_rcm_suffix() -> None:
    doc = _make_doc(
        extraction=_intra_state_purchase(),
        tax_verdict={"claim_amount": "0", "block_amount": "0",
                     "defer_amount": "232.14", "rcm_liability": "50.00"},
    )
    rows = _parse_csv(_compose_ledger_csv(documents=[doc], client_gstin=None))
    assert rows[1][9] == "DEFERRED + RCM"


def test_itc_status_pending_when_no_verdict() -> None:
    doc = _make_doc(extraction=_intra_state_purchase(), tax_verdict=None)
    rows = _parse_csv(_compose_ledger_csv(documents=[doc], client_gstin=None))
    assert rows[1][9] == "PENDING"


# ---------------------------------------------------------------------------
# Sale vs Purchase classification
# ---------------------------------------------------------------------------


def test_classified_as_purchase_when_buyer_is_client() -> None:
    doc = _make_doc(extraction=_intra_state_purchase())
    rows = _parse_csv(
        _compose_ledger_csv(documents=[doc], client_gstin="10AMEPL5872B1ZI")
    )
    assert rows[1][10] == "PURCHASE"


def test_classified_as_sale_when_vendor_is_client() -> None:
    """If the client's GSTIN matches the vendor field, it's an outbound sale."""
    doc = _make_doc(extraction=_intra_state_purchase())
    rows = _parse_csv(
        _compose_ledger_csv(documents=[doc], client_gstin="10DYKPR4180P1ZV")
    )
    assert rows[1][10] == "SALE"


def test_defaults_to_purchase_when_client_gstin_unknown() -> None:
    doc = _make_doc(extraction=_intra_state_purchase())
    rows = _parse_csv(
        _compose_ledger_csv(documents=[doc], client_gstin=None)
    )
    assert rows[1][10] == "PURCHASE"


# ---------------------------------------------------------------------------
# Logged-at IST formatting
# ---------------------------------------------------------------------------


def test_logged_at_renders_in_ist() -> None:
    """A UTC ``created_at`` of 09:30 should print as 03:00 PM IST."""
    doc = _make_doc(
        extraction=_intra_state_purchase(),
        created_at=datetime(2026, 6, 14, 9, 30, 0, tzinfo=UTC),
    )
    rows = _parse_csv(_compose_ledger_csv(documents=[doc], client_gstin=None))
    logged_at = rows[1][11]
    assert "14-Jun-2026" in logged_at
    # 09:30 UTC = 15:00 IST = 03:00 PM IST.
    assert "03:00 PM IST" in logged_at
