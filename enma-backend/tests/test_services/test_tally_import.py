"""Tally import parser tests — exercised against a real CLEIND Day Book.

The fixture ``fixtures/cleind_daybook_2025-08.xml`` is an actual export
for the CLEIND PRODUCT & SERVICE company (three Purchase vouchers, with
the ``&`` entity, per-rate GST ledgers, and Rounded Off lines). We assert
the parser normalises it correctly and that the content-hash dedup_key is
stable across re-parses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from app.services.tally_import import (
    TallyImportError,
    looks_like_tally_xml,
    parse_tally_export,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "cleind_daybook_2025-08.xml"


@pytest.fixture
def daybook_bytes() -> bytes:
    return _FIXTURE.read_bytes()


def test_sniff_recognises_tally_export(daybook_bytes: bytes) -> None:
    assert looks_like_tally_xml(daybook_bytes) is True


def test_sniff_rejects_non_tally() -> None:
    assert looks_like_tally_xml(b"%PDF-1.7\n...") is False
    assert looks_like_tally_xml(b"<html><body>hi</body></html>") is False
    assert looks_like_tally_xml(b"just some text") is False


def test_parses_company_and_voucher_count(daybook_bytes: bytes) -> None:
    export = parse_tally_export(daybook_bytes)
    assert export.company_name == "CLEIND PRODUCT & SERVICE"
    assert len(export.vouchers) == 3


def test_voucher_fields_normalised(daybook_bytes: bytes) -> None:
    export = parse_tally_export(daybook_bytes)
    # Vouchers appear in file order: 127 (Aug 17), 123 (Aug 15), 120 (Aug 6).
    v = export.vouchers[0]
    assert v.voucher_type == "Purchase"
    assert v.voucher_number == "127"
    assert v.date == datetime(2025, 8, 17, tzinfo=UTC)
    assert v.party_ledger == "CLEIND PRODUCT & SERVICE"
    assert v.event_type == "voucher_purchase"
    assert "Invoice 127" in v.narration


def test_ledger_entries_decimal_strings_no_float(daybook_bytes: bytes) -> None:
    export = parse_tally_export(daybook_bytes)
    v = export.vouchers[0]
    # Party ledger entry: +36020.00, credit (is_party, not deemed positive).
    party = next(e for e in v.ledger_entries if e["is_party"])
    assert party["ledger_name"] == "CLEIND PRODUCT & SERVICE"
    assert party["amount"] == "36020.00"
    assert party["is_deemed_positive"] is False
    # Every amount is a str (JSONB must not carry floats), and Decimal-parseable.
    for e in v.ledger_entries:
        assert isinstance(e["amount"], str)
        Decimal(e["amount"])  # raises if not a clean decimal
    # The 0.01 Rounded Off line survives.
    assert any(e["ledger_name"] == "Rounded Off" for e in v.ledger_entries)


def test_payload_shape(daybook_bytes: bytes) -> None:
    export = parse_tally_export(daybook_bytes)
    payload = export.vouchers[0].to_payload()
    assert payload["voucher_type"] == "Purchase"
    assert payload["voucher_number"] == "127"
    assert payload["date"] == "2025-08-17"
    assert payload["party_ledger"] == "CLEIND PRODUCT & SERVICE"
    assert isinstance(payload["ledger_entries"], list)
    assert len(payload["ledger_entries"]) == 8


def test_dedup_key_stable_across_reparse(daybook_bytes: bytes) -> None:
    a = parse_tally_export(daybook_bytes)
    b = parse_tally_export(daybook_bytes)
    assert [v.dedup_key for v in a.vouchers] == [v.dedup_key for v in b.vouchers]
    # Distinct vouchers get distinct keys.
    keys = [v.dedup_key for v in a.vouchers]
    assert len(set(keys)) == 3


def test_dedup_key_changes_when_amount_edited(daybook_bytes: bytes) -> None:
    edited = daybook_bytes.replace(b"36020.00", b"36021.00")
    original = parse_tally_export(daybook_bytes).vouchers[0].dedup_key
    mutated = parse_tally_export(edited).vouchers[0].dedup_key
    assert original != mutated


def test_bare_ampersand_tolerated() -> None:
    # Tally HTTP-gateway output sometimes carries a bare & — the parser
    # should escape-and-retry rather than crash.
    xml = (
        b'<?xml version="1.0" encoding="UTF-8"?><ENVELOPE><BODY><IMPORTDATA>'
        b"<REQUESTDESC><STATICVARIABLES><SVCURRENTCOMPANY>A & B Traders"
        b"</SVCURRENTCOMPANY></STATICVARIABLES></REQUESTDESC>"
        b'<REQUESTDATA><TALLYMESSAGE><VOUCHER VCHTYPE="Purchase">'
        b"<DATE>20250817</DATE><NARRATION>x</NARRATION>"
        b"<VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME><VOUCHERNUMBER>1</VOUCHERNUMBER>"
        b"<PARTYLEDGERNAME>P</PARTYLEDGERNAME>"
        b"<ALLLEDGERENTRIES.LIST><LEDGERNAME>P</LEDGERNAME>"
        b"<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><ISPARTYLEDGER>Yes</ISPARTYLEDGER>"
        b"<AMOUNT>100.00</AMOUNT></ALLLEDGERENTRIES.LIST>"
        b"</VOUCHER></TALLYMESSAGE></REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>"
    )
    export = parse_tally_export(xml)
    assert export.company_name == "A & B Traders"
    assert len(export.vouchers) == 1


def test_non_tally_xml_raises() -> None:
    with pytest.raises(TallyImportError):
        parse_tally_export(b"<html><body>not tally</body></html>")


def test_garbage_raises() -> None:
    with pytest.raises(TallyImportError):
        parse_tally_export(b"\x00\x01\x02 not xml at all")
