"""TA-2 tests — GSTR-2B parser + deterministic reconciliation engine.

Both are pure/DB-free, so we exercise them directly. The GSTR-2B JSON
fixture is hand-built to the published GSTN b2b schema (no real export
yet); validate against a real file on first upload.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from app.services.gstr2b_import import (
    Gstr2bEntry,
    Gstr2bParseError,
    looks_like_gstr2b_json,
    parse_gstr2b,
)
from app.services.recon import InvoiceRecord, reconcile

_REAL_FIXTURE = Path(__file__).parent / "fixtures" / "gstr2b_real_032026.json"


def _two_b(*invoices: dict) -> bytes:
    """Build a minimal GSTR-2B JSON with the given b2b invoices."""
    return json.dumps(
        {
            "data": {
                "rtnprd": "082025",
                "gstin": "27AABCC1234D1Z5",
                "docdata": {
                    "b2b": [
                        {
                            "ctin": "29AAACW1234F1ZX",
                            "trdnm": "Acme Supplies",
                            "inv": list(invoices),
                        }
                    ]
                },
            }
        }
    ).encode("utf-8")


def _inv(inum: str, dt: str, txval: float, cgst: float, sgst: float, itcavl: str = "Y") -> dict:
    return {
        "inum": inum,
        "dt": dt,
        "val": txval + cgst + sgst,
        "itcavl": itcavl,
        "items": [{"rt": 18, "txval": txval, "igst": 0, "cgst": cgst, "sgst": sgst, "cess": 0}],
    }


# ── parser ──────────────────────────────────────────────────────────────


def test_sniff_recognises_gstr2b() -> None:
    assert looks_like_gstr2b_json(_two_b(_inv("A1", "17-08-2025", 1000, 90, 90))) is True


def test_sniff_rejects_non_2b() -> None:
    assert looks_like_gstr2b_json(b'{"hello": "world"}') is False
    assert looks_like_gstr2b_json(b"<ENVELOPE>tally</ENVELOPE>") is False


def test_parse_basic_entry() -> None:
    parsed = parse_gstr2b(_two_b(_inv("INV-1", "17-08-2025", 1000.0, 90.0, 90.0)))
    assert parsed.recipient_gstin == "27AABCC1234D1Z5"
    assert parsed.return_period == "082025"
    assert len(parsed.entries) == 1
    e = parsed.entries[0]
    assert e.supplier_gstin == "29AAACW1234F1ZX"
    assert e.invoice_number == "INV-1"
    assert e.invoice_date == date(2025, 8, 17)
    assert e.taxable == Decimal("1000.00")
    assert e.total_itc == Decimal("180.00")  # 90 + 90
    assert e.itc_available is True


def test_parse_money_never_float_artefact() -> None:
    # 0.1 + 0.2 style: ensure stringify→Decimal path keeps clean 2dp.
    parsed = parse_gstr2b(_two_b(_inv("X", "01-08-2025", 0.1, 0.2, 0.0)))
    e = parsed.entries[0]
    assert isinstance(e.taxable, Decimal)
    assert e.taxable == Decimal("0.10")
    assert e.cgst == Decimal("0.20")


def test_parse_itc_not_available_flag() -> None:
    parsed = parse_gstr2b(_two_b(_inv("N1", "05-08-2025", 500, 45, 45, itcavl="N")))
    assert parsed.entries[0].itc_available is False


def test_parse_rejects_garbage() -> None:
    with pytest.raises(Gstr2bParseError):
        parse_gstr2b(b"not json at all")
    with pytest.raises(Gstr2bParseError):
        parse_gstr2b(b'{"data": {"foo": "bar"}}')  # no docdata


def test_parse_real_gstn_export_invoice_level_tax() -> None:
    """Regression: real GSTN b2b exports carry tax on the invoice, not in items[]."""
    parsed = parse_gstr2b(_REAL_FIXTURE.read_bytes())
    assert parsed.recipient_gstin == "10CDVPS7198R1Z7"
    assert parsed.return_period == "032026"
    assert len(parsed.entries) == 2
    by_inum = {e.invoice_number: e for e in parsed.entries}
    bandhan = by_inum["BR/TI/0046414579"]
    assert bandhan.supplier_name == "BANDHAN BANK LIMITED"
    assert bandhan.taxable == Decimal("300.00")
    assert bandhan.total_itc == Decimal("54.00")  # 27 cgst + 27 sgst
    pranay = by_inum["13/2025-26"]
    assert pranay.igst == Decimal("15608.57")
    assert pranay.total_itc == Decimal("15608.57")
    # Total recoverable if none are in the books.
    total = sum((e.total_itc for e in parsed.entries), Decimal("0"))
    assert total == Decimal("15662.57")


# ── recon engine ────────────────────────────────────────────────────────


def _entry(inum: str, dt: str, txval: float, cgst: float, sgst: float, itcavl: str = "Y"):
    return parse_gstr2b(_two_b(_inv(inum, dt, txval, cgst, sgst, itcavl))).entries[0]


def _book(inv_no: str, d: date, itc: str, gstin: str = "29AAACW1234F1ZX") -> InvoiceRecord:
    return InvoiceRecord(
        ref=inv_no,
        supplier_gstin=gstin,
        invoice_number=inv_no,
        invoice_date=d,
        itc_amount=Decimal(itc),
    )


def test_exact_match() -> None:
    books = [_book("INV-1", date(2025, 8, 17), "180.00")]
    entries = [_entry("INV-1", "17-08-2025", 1000, 90, 90)]
    r = reconcile(invoices=books, entries=entries)
    assert r.matched_count == 1
    assert r.in_books_not_in_2b_count == 0
    assert r.in_2b_not_in_books_count == 0
    assert r.recoverable_itc == Decimal("0")


def test_recoverable_itc_in_2b_not_books() -> None:
    books: list[InvoiceRecord] = []
    entries = [_entry("INV-9", "10-08-2025", 5000, 450, 450)]
    r = reconcile(invoices=books, entries=entries)
    assert r.in_2b_not_in_books_count == 1
    assert r.recoverable_itc == Decimal("900.00")  # 450 + 450


def test_in_books_not_in_2b_at_risk() -> None:
    books = [_book("ONLY-BOOKS", date(2025, 8, 3), "360.00")]
    entries: list = []
    r = reconcile(invoices=books, entries=entries)
    assert r.in_books_not_in_2b_count == 1
    assert r.at_risk_itc == Decimal("360.00")


def test_amount_mismatch() -> None:
    books = [_book("INV-2", date(2025, 8, 5), "200.00")]
    entries = [_entry("INV-2", "05-08-2025", 1000, 90, 90)]  # 2B ITC = 180
    r = reconcile(invoices=books, entries=entries)
    assert r.amount_mismatch_count == 1
    assert r.matched_count == 0
    assert r.mismatch_delta_total == Decimal("20.00")


def test_fuzzy_match_on_invoice_number_drift() -> None:
    # Books say "INV/1", 2B says "INV-1"; same supplier+date+amount → match.
    books = [_book("INV/1", date(2025, 8, 17), "180.00")]
    entries = [_entry("INV-1", "17-08-2025", 1000, 90, 90)]
    r = reconcile(invoices=books, entries=entries)
    assert r.matched_count == 1
    assert r.in_books_not_in_2b_count == 0
    assert r.in_2b_not_in_books_count == 0


def test_tolerance_one_rupee() -> None:
    books = [_book("INV-3", date(2025, 8, 9), "180.50")]  # 0.50 within ₹1
    entries = [_entry("INV-3", "09-08-2025", 1000, 90, 90)]  # 180.00
    r = reconcile(invoices=books, entries=entries)
    assert r.matched_count == 1


# ── credit / debit notes (cdnr) ───────────────────────────────────────────


def _cdnr_file(*notes: dict) -> bytes:
    """Build a minimal GSTR-2B JSON with a cdnr section and no b2b."""
    return json.dumps(
        {
            "data": {
                "rtnprd": "082025",
                "gstin": "27AABCC1234D1Z5",
                "docdata": {
                    "cdnr": [
                        {
                            "ctin": "29AAACW1234F1ZX",
                            "trdnm": "Acme Supplies",
                            "nt": list(notes),
                        }
                    ]
                },
            }
        }
    ).encode("utf-8")


def _note(ntnum: str, dt: str, typ: str, txval: float, cgst: float, sgst: float) -> dict:
    return {
        "ntnum": ntnum,
        "nt_dt": dt,
        "typ": typ,
        "val": txval + cgst + sgst,
        "itcavl": "Y",
        "items": [{"rt": 18, "txval": txval, "igst": 0, "cgst": cgst, "sgst": sgst, "cess": 0}],
    }


def test_parse_credit_note_is_signed_negative() -> None:
    parsed = parse_gstr2b(_cdnr_file(_note("CN-1", "20-08-2025", "C", 1000.0, 90.0, 90.0)))
    assert len(parsed.entries) == 1
    e = parsed.entries[0]
    assert e.note_type == "C"
    assert e.cgst == Decimal("-90.00")
    assert e.total_itc == Decimal("-180.00")  # credit note reduces ITC
    assert e.invoice_value == Decimal("-1180.00")


def test_parse_debit_note_is_signed_positive() -> None:
    parsed = parse_gstr2b(_cdnr_file(_note("DN-1", "21-08-2025", "D", 500.0, 45.0, 45.0)))
    e = parsed.entries[0]
    assert e.note_type == "D"
    assert e.total_itc == Decimal("90.00")  # debit note adds ITC


def test_b2b_and_cdnr_parse_together() -> None:
    # A file with both sections: parse_gstr2b reads b2b then cdnr.
    raw = json.loads(_two_b(_inv("INV-1", "17-08-2025", 1000.0, 90.0, 90.0)))
    raw["data"]["docdata"]["cdnr"] = [
        {
            "ctin": "29AAACW1234F1ZX",
            "trdnm": "Acme Supplies",
            "nt": [_note("CN-9", "18-08-2025", "C", 100.0, 9.0, 9.0)],
        }
    ]
    parsed = parse_gstr2b(json.dumps(raw).encode("utf-8"))
    kinds = sorted((e.note_type or "INV") for e in parsed.entries)
    assert kinds == ["C", "INV"]


def test_cdnr_payload_round_trip_preserves_sign_and_type() -> None:
    e = parse_gstr2b(_cdnr_file(_note("CN-2", "20-08-2025", "C", 1000.0, 90.0, 90.0))).entries[0]
    rebuilt = Gstr2bEntry.from_payload(e.to_payload())
    assert rebuilt.note_type == "C"
    assert rebuilt.total_itc == Decimal("-180.00")
    assert rebuilt.dedup_key == e.dedup_key  # key stable across the round trip


def test_recon_credit_note_nets_down_recoverable() -> None:
    # An unclaimed invoice (+180) and an unreflected credit note (-90) for the
    # same supplier → net recoverable is 90, not 180.
    entries = [
        _entry("INV-1", "17-08-2025", 1000.0, 90.0, 90.0),
        parse_gstr2b(_cdnr_file(_note("CN-1", "18-08-2025", "C", 500.0, 45.0, 45.0))).entries[0],
    ]
    r = reconcile(invoices=[], entries=entries)
    assert r.in_2b_not_in_books_count == 2
    assert r.recoverable_itc == Decimal("90.00")  # 180 - 90
