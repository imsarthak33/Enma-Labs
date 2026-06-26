"""GSTR-2B JSON parser — the *available-ITC* leg of Tri-Way recon (ADR-015).

A CA downloads their client's GSTR-2B for a return period from the GST
portal (a JSON file) and uploads it to Enma. This module parses that
JSON into normalised supplier-invoice entries that the reconciliation
engine matches against the client's books (the ``documents`` table).

Scope
-----
We parse the ``b2b`` section (regular supplier invoices) and the ``cdnr``
section (credit/debit notes received). A **credit note** reduces available
ITC and a **debit note** increases it, so note taxes are stored *signed*
(credit → negative) and flow through the recon's available-ITC math
correctly — b2b-only silently overstated recoverable ITC by ignoring credit
notes. Amendments (``b2ba``/``cdnra``) remain a fast follow; the parser
ignores unknown sections rather than failing.

Determinism + safety
--------------------
Pure parsing, no LLM (matching tax credit is a filing-correctness
operation). Money comes off the JSON as floats — we stringify before
:func:`parse_money` so a binary-float artefact never enters a tax figure.
The parser is defensive: missing keys yield skips, not crashes.

Match key
---------
``dedup_key`` (and the recon match key) is the same natural key the
``documents`` table dedupes on:
``SHA-256(UPPER(supplier_gstin) | UPPER(invoice_no) | ISO-date)``. The
GST portal emits dates as ``dd-mm-yyyy``; we normalise to ISO so the key
lines up with the ``documents`` side regardless of source format.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.utils.decimal_utils import ZERO, parse_money

__all__ = [
    "Gstr2bEntry",
    "Gstr2bParseError",
    "ParsedGstr2b",
    "looks_like_gstr2b_json",
    "parse_gstr2b",
]


class Gstr2bParseError(ValueError):
    """Raised when bytes are not a parseable GSTR-2B JSON document."""


@dataclass(frozen=True)
class Gstr2bEntry:
    """One supplier invoice line from a GSTR-2B b2b section."""

    supplier_gstin: str
    supplier_name: str
    invoice_number: str
    invoice_date: date
    invoice_value: Decimal
    taxable: Decimal
    igst: Decimal
    cgst: Decimal
    sgst: Decimal
    cess: Decimal
    itc_available: bool
    dedup_key: str
    note_type: str | None = None  # None=invoice | 'C'=credit note | 'D'=debit note

    @property
    def total_itc(self) -> Decimal:
        """Net credit on this line (IGST + CGST + SGST + cess).

        Already signed: a credit note's tax fields are negative, so the sum
        is the *net* ITC effect (reduction) of the line.
        """
        return self.igst + self.cgst + self.sgst + self.cess

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Gstr2bEntry:
        """Rebuild an entry from a stored brain_event payload (on-demand recon)."""
        supplier_gstin = str(payload.get("supplier_gstin") or "").strip().upper()
        invoice_number = str(payload.get("invoice_number") or "").strip()
        inv_date = _parse_2b_date(payload.get("invoice_date"))
        raw_note = payload.get("note_type")
        note_type = str(raw_note).strip().upper() if raw_note else None
        return cls(
            supplier_gstin=supplier_gstin,
            supplier_name=str(payload.get("supplier_name") or ""),
            invoice_number=invoice_number,
            invoice_date=inv_date,
            invoice_value=_money(payload.get("invoice_value")),
            taxable=_money(payload.get("taxable")),
            igst=_money(payload.get("igst")),
            cgst=_money(payload.get("cgst")),
            sgst=_money(payload.get("sgst")),
            cess=_money(payload.get("cess")),
            itc_available=bool(payload.get("itc_available", True)),
            dedup_key=_content_key(
                supplier_gstin, invoice_number, inv_date.isoformat(), note_type
            ),
            note_type=note_type,
        )

    def to_payload(self) -> dict[str, Any]:
        """JSONB body for a brain_event. All money as strings (Decimal-safe)."""
        return {
            "supplier_gstin": self.supplier_gstin,
            "supplier_name": self.supplier_name,
            "invoice_number": self.invoice_number,
            "invoice_date": self.invoice_date.isoformat(),
            "invoice_value": str(self.invoice_value),
            "taxable": str(self.taxable),
            "igst": str(self.igst),
            "cgst": str(self.cgst),
            "sgst": str(self.sgst),
            "cess": str(self.cess),
            "total_itc": str(self.total_itc),
            "itc_available": self.itc_available,
            "note_type": self.note_type,
        }


@dataclass(frozen=True)
class ParsedGstr2b:
    """A whole GSTR-2B file: recipient + return period + supplier entries."""

    recipient_gstin: str
    return_period: str
    entries: tuple[Gstr2bEntry, ...]


def _money(value: Any) -> Decimal:
    """JSON number/string → 2dp Decimal. Stringify first — never float→Decimal."""
    if value is None or value == "":
        return ZERO
    try:
        return parse_money(str(value))
    except (ValueError, TypeError):
        return ZERO


def _parse_2b_date(raw: Any) -> date:
    """GST portal date ``dd-mm-yyyy`` → :class:`date`. Tolerates ISO too."""
    text = str(raw).strip()
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise Gstr2bParseError(f"unparseable GSTR-2B date: {raw!r}")


def _content_key(
    supplier_gstin: str,
    invoice_number: str,
    iso_date: str,
    note_type: str | None = None,
) -> str:
    """Natural-key hash (gstin|invno|date), the same the documents table uses.

    A credit/debit note carries its own ``note_type`` segment so a note and
    an invoice that happen to share (gstin, number, date) never collide.
    """
    payload = f"{supplier_gstin.strip().upper()}|{invoice_number.strip().upper()}|{iso_date}"
    if note_type:
        payload += f"|{note_type}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _extract_taxes(obj: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    """Pull (taxable, igst, cgst, sgst, cess) from a 2B invoice/note object.

    Real GSTN exports carry the consolidated tax fields directly on the
    object; some variants nest them in an item array (``items``/``itms``).
    Prefer the item array when present and non-empty, else the object level.
    """
    items = obj.get("items") or obj.get("itms")
    taxable = igst = cgst = sgst = cess = ZERO
    if isinstance(items, list) and items:
        for it in items:
            if not isinstance(it, dict):
                continue
            taxable += _money(it.get("txval"))
            igst += _money(it.get("igst"))
            cgst += _money(it.get("cgst"))
            sgst += _money(it.get("sgst"))
            cess += _money(it.get("cess"))
    else:
        taxable = _money(obj.get("txval"))
        igst = _money(obj.get("igst"))
        cgst = _money(obj.get("cgst"))
        sgst = _money(obj.get("sgst"))
        cess = _money(obj.get("cess"))
    return taxable, igst, cgst, sgst, cess


def _load_root(raw: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, TypeError) as exc:
        raise Gstr2bParseError("not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise Gstr2bParseError("GSTR-2B root must be a JSON object")
    return parsed


def _docdata(root: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    """Pull (docdata, recipient_gstin, return_period) from either layout.

    The portal wraps the payload under ``data`` in the GET response, but
    some exports hand back the inner object directly. Handle both.
    """
    data_obj = root.get("data")
    data: dict[str, Any] = data_obj if isinstance(data_obj, dict) else root
    docdata = data.get("docdata")
    if not isinstance(docdata, dict):
        raise Gstr2bParseError("no docdata section — not a GSTR-2B document")
    recipient = str(data.get("gstin") or "").strip().upper()
    period = str(data.get("rtnprd") or data.get("retperiod") or "").strip()
    return docdata, recipient, period


def looks_like_gstr2b_json(raw: bytes) -> bool:
    """Cheap content sniff: is this a GSTR-2B JSON export? (head only)."""
    head = raw[:4096].lstrip()
    if head[:1] not in (b"{", b"\xef"):  # JSON object or UTF-8 BOM
        return False
    try:
        text = raw[:8192].decode("utf-8", errors="replace").lower()
    except Exception:
        return False
    return "docdata" in text and (
        "rtnprd" in text or '"b2b"' in text or '"cdnr"' in text
    )


def parse_gstr2b(raw: bytes) -> ParsedGstr2b:
    """Parse a GSTR-2B JSON export into normalised supplier entries.

    Raises :class:`Gstr2bParseError` when the bytes are not a GSTR-2B
    document. Individual malformed invoices are skipped, not fatal.
    """
    root = _load_root(raw)
    docdata, recipient_gstin, return_period = _docdata(root)

    entries: list[Gstr2bEntry] = []
    _parse_b2b(docdata.get("b2b"), entries)
    _parse_cdnr(docdata.get("cdnr"), entries)

    if not entries:
        raise Gstr2bParseError(
            "GSTR-2B had no parseable b2b invoices or credit/debit notes"
        )

    return ParsedGstr2b(
        recipient_gstin=recipient_gstin,
        return_period=return_period,
        entries=tuple(entries),
    )


def _parse_b2b(b2b: Any, out: list[Gstr2bEntry]) -> None:
    """Parse the b2b (regular supplier invoice) section into ``out``."""
    if not isinstance(b2b, list):
        return
    for supplier in b2b:
        if not isinstance(supplier, dict):
            continue
        ctin = str(supplier.get("ctin") or "").strip().upper()
        invoices = supplier.get("inv")
        if not ctin or not isinstance(invoices, list):
            continue
        trade_name = str(supplier.get("trdnm") or "").strip()
        for inv in invoices:
            if not isinstance(inv, dict):
                continue
            inum = str(inv.get("inum") or "").strip()
            if not inum:
                continue
            try:
                inv_date = _parse_2b_date(inv.get("dt"))
            except Gstr2bParseError:
                continue
            taxable, igst, cgst, sgst, cess = _extract_taxes(inv)
            iso = inv_date.isoformat()
            out.append(
                Gstr2bEntry(
                    supplier_gstin=ctin,
                    supplier_name=trade_name,
                    invoice_number=inum,
                    invoice_date=inv_date,
                    invoice_value=_money(inv.get("val")),
                    taxable=taxable,
                    igst=igst,
                    cgst=cgst,
                    sgst=sgst,
                    cess=cess,
                    itc_available=str(inv.get("itcavl") or "Y").strip().upper() != "N",
                    dedup_key=_content_key(ctin, inum, iso),
                )
            )


def _parse_cdnr(cdnr: Any, out: list[Gstr2bEntry]) -> None:
    """Parse the cdnr (credit/debit notes received) section into ``out``.

    A credit note (``typ='C'``) reduces ITC, a debit note (``typ='D'``)
    increases it — so the note's money fields are stored *signed* (credit
    negated). Field names vary across portal versions; we accept the common
    aliases (``ntnum``/``nt_num``, ``nt_dt``/``ntdt``, ``typ``/``ntty``).
    """
    if not isinstance(cdnr, list):
        return
    for supplier in cdnr:
        if not isinstance(supplier, dict):
            continue
        ctin = str(supplier.get("ctin") or "").strip().upper()
        notes = supplier.get("nt")
        if not ctin or not isinstance(notes, list):
            continue
        trade_name = str(supplier.get("trdnm") or "").strip()
        for nt in notes:
            if not isinstance(nt, dict):
                continue
            ntnum = str(nt.get("ntnum") or nt.get("nt_num") or nt.get("inum") or "").strip()
            if not ntnum:
                continue
            try:
                nt_date = _parse_2b_date(nt.get("nt_dt") or nt.get("ntdt") or nt.get("dt"))
            except Gstr2bParseError:
                continue
            typ = str(nt.get("typ") or nt.get("ntty") or "C").strip().upper()
            typ = "D" if typ.startswith("D") else "C"
            sign = Decimal("1") if typ == "D" else Decimal("-1")
            taxable, igst, cgst, sgst, cess = _extract_taxes(nt)
            iso = nt_date.isoformat()
            out.append(
                Gstr2bEntry(
                    supplier_gstin=ctin,
                    supplier_name=trade_name,
                    invoice_number=ntnum,
                    invoice_date=nt_date,
                    invoice_value=_money(nt.get("val")) * sign,
                    taxable=taxable * sign,
                    igst=igst * sign,
                    cgst=cgst * sign,
                    sgst=sgst * sign,
                    cess=cess * sign,
                    itc_available=str(nt.get("itcavl") or "Y").strip().upper() != "N",
                    dedup_key=_content_key(ctin, ntnum, iso, typ),
                    note_type=typ,
                )
            )
