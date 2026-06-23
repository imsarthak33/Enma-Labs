"""Tally Prime ENVELOPE XML parser — the *read* side of Tally integration.

The mirror of :mod:`app.services.tally`, which *writes* the Tally import
ENVELOPE. This module *reads* one back: a CA exports their Day Book (or a
voucher report) from Tally Prime as XML and sends it to Enma; we parse it
into normalised vouchers that the Brain-ingestion handler turns into
``brain_events`` rows (ADR-014).

Why a dedicated parser (not the document/invoice pipeline)
----------------------------------------------------------
A Tally export is structured accounting data, not a scanned invoice.
There is nothing to OCR and no tax math to (re)compute here — Tally has
already done both. So this is pure, deterministic Python parsing; the
"LLM does OCR, Python does math" rule means the LLM never touches this
path.

Security
--------
The XML is user-supplied, so we parse with :mod:`defusedxml` to neutralise
the classic XML attacks (external entity / billion-laughs). We also cap
input size at the call site.

Idempotent re-ingest
--------------------
Tally's manual export carries no stable ``<GUID>`` (our own writer in
:mod:`app.services.tally` doesn't emit one either). So the ``dedup_key``
is a SHA-256 of the voucher's *content* — type, number, date, narration,
and every ledger line. This mirrors the ``documents.content_hash``
convention (W3): re-uploading the identical Day Book is a no-op, while a
voucher that was genuinely edited in Tally and re-exported hashes
differently and lands as a new observation (the brain is an append-only
event log; an edit is a new fact, not a mutation).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

from defusedxml.ElementTree import fromstring as safe_fromstring

from app.utils.decimal_utils import parse_money

__all__ = [
    "ParsedTallyExport",
    "ParsedVoucher",
    "TallyImportError",
    "parse_tally_export",
]


class TallyImportError(ValueError):
    """Raised when the bytes are not a parseable Tally voucher export."""


# Tally serialises dates as YYYYMMDD (e.g. 20250817).
_TALLY_DATE_LEN: Final[int] = 8


@dataclass(frozen=True)
class ParsedVoucher:
    """One ``<VOUCHER>`` normalised for Brain ingestion.

    ``amounts`` are :class:`Decimal` internally but serialise to strings
    in :meth:`to_payload` — JSONB must never carry a float for money.
    """

    voucher_type: str
    voucher_number: str
    date: datetime
    narration: str
    party_ledger: str
    ledger_entries: tuple[dict[str, Any], ...]
    dedup_key: str = field(compare=False)

    @property
    def event_type(self) -> str:
        """``voucher_purchase`` / ``voucher_sales`` / … — adapter event name."""
        slug = "".join(
            c if c.isalnum() else "_" for c in self.voucher_type.strip().lower()
        ).strip("_")
        return f"voucher_{slug or 'unknown'}"

    def to_payload(self) -> dict[str, Any]:
        """Normalised JSONB body. All money as strings (Decimal-safe)."""
        return {
            "voucher_type": self.voucher_type,
            "voucher_number": self.voucher_number,
            "date": self.date.date().isoformat(),
            "narration": self.narration,
            "party_ledger": self.party_ledger,
            "ledger_entries": list(self.ledger_entries),
        }


@dataclass(frozen=True)
class ParsedTallyExport:
    """A whole Tally export: the company it targets + its vouchers."""

    company_name: str
    vouchers: tuple[ParsedVoucher, ...]


def _text(node: Any, tag: str, default: str = "") -> str:
    """Return the stripped text of ``node``'s first ``tag`` child, or default."""
    child = node.find(tag)
    if child is None or child.text is None:
        return default
    return child.text.strip()


def _parse_tally_date(raw: str) -> datetime:
    """``YYYYMMDD`` → tz-aware UTC midnight datetime.

    ``occurred_at`` on the brain_event is the *voucher* date and is part of
    the dedup key, so it must be derived deterministically from the source.
    """
    cleaned = raw.strip()
    if len(cleaned) != _TALLY_DATE_LEN or not cleaned.isdigit():
        raise TallyImportError(f"unparseable Tally date: {raw!r}")
    try:
        return datetime(
            int(cleaned[0:4]), int(cleaned[4:6]), int(cleaned[6:8]), tzinfo=UTC
        )
    except ValueError as exc:
        raise TallyImportError(f"invalid Tally date: {raw!r}") from exc


def _parse_ledger_entries(voucher: Any) -> list[dict[str, Any]]:
    """Pull every ``<ALLLEDGERENTRIES.LIST>`` into normalised dicts.

    Amounts run through :func:`parse_money` (Decimal, never float) and are
    stored as strings. Tally's sign convention is preserved verbatim:
    negative = debit slice, positive = party credit.
    """
    entries: list[dict[str, Any]] = []
    for le in voucher.findall("ALLLEDGERENTRIES.LIST"):
        ledger_name = _text(le, "LEDGERNAME")
        raw_amount = _text(le, "AMOUNT")
        if not ledger_name and not raw_amount:
            continue
        try:
            amount = parse_money(raw_amount) if raw_amount else Decimal("0")
        except (ValueError, TypeError) as exc:
            raise TallyImportError(
                f"unparseable AMOUNT {raw_amount!r} on ledger {ledger_name!r}"
            ) from exc
        entries.append(
            {
                "ledger_name": ledger_name,
                "amount": str(amount),
                "is_party": _text(le, "ISPARTYLEDGER").lower() == "yes",
                "is_deemed_positive": _text(le, "ISDEEMEDPOSITIVE").lower() == "yes",
            }
        )
    return entries


def _compute_dedup_key(
    *,
    voucher_type: str,
    voucher_number: str,
    date: datetime,
    narration: str,
    ledger_entries: list[dict[str, Any]],
) -> str:
    """SHA-256 of canonical voucher content — stable across re-exports.

    Ledger entries are sorted so emission order cannot change the hash;
    amounts are already canonical 2-dp strings from :func:`parse_money`.
    """
    ledger_canonical = "|".join(
        sorted(f"{e['ledger_name']}={e['amount']}" for e in ledger_entries)
    )
    canonical = "␟".join(
        [
            voucher_type,
            voucher_number,
            date.date().isoformat(),
            narration,
            ledger_canonical,
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _coerce_to_root(raw: bytes) -> Any:
    """Parse bytes to an XML root, tolerating Tally's encoding quirks.

    ElementTree honours the ``<?xml encoding?>`` declaration for bytes.
    Real Tally HTTP-gateway output occasionally contains a stray bare
    ``&``; if the strict parse fails we retry once with bare ampersands
    escaped before giving up.
    """
    try:
        return safe_fromstring(raw)
    except Exception:  # noqa: BLE001 — any parse failure triggers the cleanup retry
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            raise TallyImportError("could not decode Tally XML bytes") from exc
        # Escape bare & that are not already part of an entity.
        import re

        cleaned = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", "&amp;", text)
        try:
            return safe_fromstring(cleaned)
        except Exception as exc:  # noqa: BLE001
            raise TallyImportError("not valid XML") from exc


def looks_like_tally_xml(raw: bytes) -> bool:
    """Cheap content sniff: is this a Tally voucher ENVELOPE export?

    Used by the worker to branch an uploaded file into Tally ingestion
    instead of the invoice extractor. Sniffs the head only — no full parse.
    """
    head = raw[:4096].lstrip()
    if not head[:1] in (b"<", b"\xef", b"\xff", b"\xfe"):  # XML / BOM start
        return False
    try:
        text = raw[:4096].decode("utf-8", errors="replace").upper()
    except Exception:  # noqa: BLE001
        return False
    return "<ENVELOPE" in text and ("TALLYMESSAGE" in text or "<VOUCHER" in text)


def parse_tally_export(raw: bytes) -> ParsedTallyExport:
    """Parse a Tally voucher-export ENVELOPE into normalised vouchers.

    Raises :class:`TallyImportError` when the bytes are not a Tally
    voucher export or a voucher is malformed (bad date / amount). Vouchers
    with no ledger entries at all are skipped (Tally sometimes emits empty
    master rows in a mixed export).
    """
    root = _coerce_to_root(raw)

    company_name = ""
    company_node = root.find(".//SVCURRENTCOMPANY")
    if company_node is not None and company_node.text:
        company_name = company_node.text.strip()

    voucher_nodes = root.findall(".//VOUCHER")
    if not voucher_nodes:
        raise TallyImportError("no <VOUCHER> elements found — not a Tally voucher export")

    vouchers: list[ParsedVoucher] = []
    for vnode in voucher_nodes:
        ledger_entries = _parse_ledger_entries(vnode)
        if not ledger_entries:
            continue
        voucher_type = _text(vnode, "VOUCHERTYPENAME") or vnode.get("VCHTYPE", "Unknown")
        voucher_number = _text(vnode, "VOUCHERNUMBER")
        narration = _text(vnode, "NARRATION")
        party_ledger = _text(vnode, "PARTYLEDGERNAME")
        date = _parse_tally_date(_text(vnode, "DATE"))
        dedup_key = _compute_dedup_key(
            voucher_type=voucher_type,
            voucher_number=voucher_number,
            date=date,
            narration=narration,
            ledger_entries=ledger_entries,
        )
        vouchers.append(
            ParsedVoucher(
                voucher_type=voucher_type,
                voucher_number=voucher_number,
                date=date,
                narration=narration,
                party_ledger=party_ledger,
                ledger_entries=tuple(ledger_entries),
                dedup_key=dedup_key,
            )
        )

    if not vouchers:
        raise TallyImportError("export had vouchers but none carried ledger entries")

    return ParsedTallyExport(company_name=company_name, vouchers=tuple(vouchers))
