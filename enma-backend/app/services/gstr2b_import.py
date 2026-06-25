"""GSTR-2B JSON parser — the *available-ITC* leg of Tri-Way recon (ADR-015).

A CA downloads their client's GSTR-2B for a return period from the GST
portal (a JSON file) and uploads it to Enma. This module parses that
JSON into normalised supplier-invoice entries that the reconciliation
engine matches against the client's books (the ``documents`` table).

Scope (MVP)
-----------
We parse the ``b2b`` section — regular supplier invoices, which carry the
bulk of input-tax credit. Credit/debit notes (``cdnr``) and amendments
(``b2ba``/``cdnra``) are a fast follow; the parser ignores unknown
sections rather than failing.

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

    @property
    def total_itc(self) -> Decimal:
        """Total credit on this invoice (IGST + CGST + SGST + cess)."""
        return self.igst + self.cgst + self.sgst + self.cess

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Gstr2bEntry:
        """Rebuild an entry from a stored brain_event payload (on-demand recon)."""
        supplier_gstin = str(payload.get("supplier_gstin") or "").strip().upper()
        invoice_number = str(payload.get("invoice_number") or "").strip()
        inv_date = _parse_2b_date(payload.get("invoice_date"))
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
            dedup_key=_content_key(supplier_gstin, invoice_number, inv_date.isoformat()),
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


def _content_key(supplier_gstin: str, invoice_number: str, iso_date: str) -> str:
    """Same natural-key hash the documents table uses (gstin|invno|date)."""
    payload = f"{supplier_gstin.strip().upper()}|{invoice_number.strip().upper()}|{iso_date}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    return "docdata" in text and ("rtnprd" in text or '"b2b"' in text)


def parse_gstr2b(raw: bytes) -> ParsedGstr2b:  # noqa: PLR0912 — defensive parsing is branchy
    """Parse a GSTR-2B JSON export into normalised supplier entries.

    Raises :class:`Gstr2bParseError` when the bytes are not a GSTR-2B
    document. Individual malformed invoices are skipped, not fatal.
    """
    root = _load_root(raw)
    docdata, recipient_gstin, return_period = _docdata(root)

    b2b = docdata.get("b2b")
    if not isinstance(b2b, list):
        raise Gstr2bParseError("no b2b section in docdata")

    entries: list[Gstr2bEntry] = []
    for supplier in b2b:
        if not isinstance(supplier, dict):
            continue
        ctin = str(supplier.get("ctin") or "").strip().upper()
        trade_name = str(supplier.get("trdnm") or "").strip()
        invoices = supplier.get("inv")
        if not ctin or not isinstance(invoices, list):
            continue
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
            # Real GSTN b2b exports carry the consolidated tax fields
            # directly on the invoice object. Some variants nest them in an
            # item array (``items``/``itms``) — prefer that when present and
            # non-empty, else fall back to the invoice-level figures.
            items = inv.get("items") or inv.get("itms")
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
                taxable = _money(inv.get("txval"))
                igst = _money(inv.get("igst"))
                cgst = _money(inv.get("cgst"))
                sgst = _money(inv.get("sgst"))
                cess = _money(inv.get("cess"))
            iso = inv_date.isoformat()
            entries.append(
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

    if not entries:
        raise Gstr2bParseError("GSTR-2B b2b section had no parseable invoices")

    return ParsedGstr2b(
        recipient_gstin=recipient_gstin,
        return_period=return_period,
        entries=tuple(entries),
    )
