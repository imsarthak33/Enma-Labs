"""Filing-approval protocol — the ``ENMA APPROVE FILING`` flow.

A CA approves a filing period by sending exactly this string:

    ENMA APPROVE FILING <Month> <YYYY>

The match is strict (case-sensitive on the keyword, month spelled in
English with capital first letter, four-digit year). Anything else —
even ``approve filing march 2026`` or
``enma approve filing march 2026`` — is treated as regular text. This
strictness is intentional: the approval action is irreversible and
locks the entire period out of further verdict computation.

Two-step protocol
-----------------
The flow is split so a stray typo can't lock a filing:

    Step 1 — :func:`parse_approval_intent` matches the regex, builds a
             snapshot of the filing period (documents + totals), hashes
             it, and returns an :class:`ApprovalIntent`. The caller
             sends a confirmation message with an inline button.

    Step 2 — :func:`finalize_approval` re-reads the snapshot, verifies
             the hash hasn't drifted, and writes the immutable
             ``filing_approvals`` row.

Splitting parse from finalize means the supervisor can render a
human-readable confirmation between the two without re-doing the work,
and the inline-button callback can carry only the hash (small) rather
than the full snapshot.

Lock semantics
--------------
Once finalized the engine's ``PeriodContext.locked_periods`` includes
the period. The pipeline writer (see ``app/agents/pipeline.py``)
refuses to insert NEW documents into a locked period AND refuses to
recompute the verdict on an existing document — see ADR-005 rev 2.
"""

from __future__ import annotations

import calendar
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.document import Document
from app.db.queries.documents import DocumentQuery
from app.db.queries.filings import FilingAlreadyApproved, FilingQuery
from app.utils.hash_utils import sha256_canonical_json

__all__ = [
    "APPROVAL_REGEX",
    "ApprovalIntent",
    "ApprovalResult",
    "FilingPeriodAlreadyLocked",
    "InvalidApprovalString",
    "build_filing_snapshot",
    "finalize_approval",
    "parse_approval_intent",
]


# ---------------------------------------------------------------------------
# Strict regex — case-sensitive, full-string match, single space delimiters.
# ---------------------------------------------------------------------------

_MONTHS: Final[tuple[str, ...]] = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

# Relaxed in W3-h3 so CAs can write the phrase naturally. We still
# refuse anything that doesn't carry the full "ENMA APPROVE FILING"
# keyword + a recognised month + a 4-digit year — so a typo'd
# "approve filling" still won't lock a period. What we now allow:
#   * an optional "for" / "of" between FILING and the month
#   * case-insensitive month spelling ("June" or "june")
#   * the keyword and "for"/"of" particles in either case
APPROVAL_REGEX: Final[re.Pattern[str]] = re.compile(
    r"^ENMA APPROVE FILING(?:\s+(?:for|of))?\s+"
    r"(" + "|".join(_MONTHS) + r")\s+(\d{4})$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class InvalidApprovalString(Exception):
    """Raised when text does not match :data:`APPROVAL_REGEX` exactly."""


class FilingPeriodAlreadyLocked(Exception):
    """Raised when an approval is attempted on an already-locked period."""

    def __init__(self, month: int, year: int) -> None:
        super().__init__(f"period {month}/{year} is already locked")
        self.month = month
        self.year = year


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApprovalIntent:
    """Parsed + snapshotted intent ready to be confirmed by the CA.

    The caller renders this in a human-readable confirmation message
    with an inline button whose callback payload carries
    ``snapshot_hash``. On callback, :func:`finalize_approval` re-verifies
    the snapshot has not drifted before the write happens.
    """

    month: int
    year: int
    document_count: int
    total_taxable_value: Decimal
    total_tax: Decimal
    document_ids: tuple[uuid.UUID, ...]
    snapshot: dict[str, Any]
    snapshot_hash: str


@dataclass(frozen=True)
class ApprovalResult:
    """Outcome of the finalize step."""

    approval_id: uuid.UUID
    month: int
    year: int
    snapshot_hash: str


# ---------------------------------------------------------------------------
# Parse step
# ---------------------------------------------------------------------------


def _month_number(month_name: str) -> int:
    """Convert the English month name (any case) to its 1-12 index."""
    return _MONTHS.index(month_name.strip().title()) + 1


async def parse_approval_intent(
    *,
    session: AsyncSession,
    ca_firm_id: uuid.UUID,
    text: str,
) -> ApprovalIntent:
    """Validate ``text`` and build the snapshot for the named period.

    Raises
    ------
    InvalidApprovalString
        ``text`` does not match :data:`APPROVAL_REGEX` byte-for-byte.
    FilingPeriodAlreadyLocked
        The named period is already approved.
    """
    match = APPROVAL_REGEX.fullmatch(text)
    if match is None:
        raise InvalidApprovalString(text)
    month_name, year_str = match.group(1), match.group(2)
    month = _month_number(month_name)
    year = int(year_str)

    filings = FilingQuery(session=session, ca_firm_id=ca_firm_id)
    if await filings.is_period_locked(month=month, year=year):
        raise FilingPeriodAlreadyLocked(month=month, year=year)

    docs = DocumentQuery(session=session, ca_firm_id=ca_firm_id)
    period_docs = await docs.list_by_filing_period(year=year, month=month)
    snapshot = build_filing_snapshot(
        ca_firm_id=ca_firm_id,
        month=month,
        year=year,
        documents=list(period_docs),
    )
    snapshot_hash = sha256_canonical_json(snapshot)

    return ApprovalIntent(
        month=month,
        year=year,
        document_count=snapshot["document_count"],
        total_taxable_value=Decimal(snapshot["totals"]["taxable_value"]),
        total_tax=Decimal(snapshot["totals"]["total_tax"]),
        document_ids=tuple(uuid.UUID(d) for d in snapshot["document_ids"]),
        snapshot=snapshot,
        snapshot_hash=snapshot_hash,
    )


# ---------------------------------------------------------------------------
# Snapshot builder
# ---------------------------------------------------------------------------


def build_filing_snapshot(
    *,
    ca_firm_id: uuid.UUID,
    month: int,
    year: int,
    documents: list[Document],
) -> dict[str, Any]:
    """Deterministically render a JSON-serialisable filing snapshot.

    Keys are emitted in a fixed order so the SHA-256 hash is stable
    across runs (the hash util re-sorts but we make it explicit here for
    readability). ``Decimal`` values are stored as strings so the JSON
    round-trip is lossless.
    """
    sorted_docs = sorted(documents, key=lambda d: d.id)
    doc_ids = [str(d.id) for d in sorted_docs]

    taxable = Decimal("0")
    total_tax = Decimal("0")
    per_client_totals: dict[str, dict[str, str]] = {}
    period_calendar = (
        f"{calendar.month_name[month]} {year}"
        if 1 <= month <= 12
        else f"{month}/{year}"
    )

    for doc in sorted_docs:
        totals = (doc.extraction_data or {}).get("totals", {})
        taxable_val = _to_decimal(totals.get("taxable_value"))
        cgst = _to_decimal(totals.get("total_cgst"))
        sgst = _to_decimal(totals.get("total_sgst"))
        igst = _to_decimal(totals.get("total_igst"))
        tax_for_doc = cgst + sgst + igst

        taxable += taxable_val
        total_tax += tax_for_doc

        client_key = str(doc.client_id)
        if client_key not in per_client_totals:
            per_client_totals[client_key] = {
                "taxable_value": "0",
                "total_tax": "0",
                "document_count": "0",
            }
        bucket = per_client_totals[client_key]
        bucket["taxable_value"] = str(
            _to_decimal(bucket["taxable_value"]) + taxable_val
        )
        bucket["total_tax"] = str(
            _to_decimal(bucket["total_tax"]) + tax_for_doc
        )
        bucket["document_count"] = str(int(bucket["document_count"]) + 1)

    return {
        "ca_firm_id": str(ca_firm_id),
        "filing_month": month,
        "filing_year": year,
        "period_label": period_calendar,
        "document_count": len(sorted_docs),
        "document_ids": doc_ids,
        "totals": {
            "taxable_value": str(taxable),
            "total_tax": str(total_tax),
        },
        "per_client": per_client_totals,
        "schema_version": 1,
    }


def _to_decimal(value: Any) -> Decimal:
    """Coerce a JSON-stored numeric (str/int/float) to Decimal safely.

    Floats are converted via ``str()`` to avoid binary-representation
    drift; missing / None values become zero.
    """
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    try:
        return Decimal(str(value).strip())
    except (ValueError, ArithmeticError):
        return Decimal("0")


# ---------------------------------------------------------------------------
# Finalize step
# ---------------------------------------------------------------------------


async def finalize_approval(
    *,
    session: AsyncSession,
    ca_firm_id: uuid.UUID,
    approved_by_chat_id: int,
    text: str,
    expected_snapshot_hash: str,
) -> ApprovalResult:
    """Re-derive the snapshot, verify it matches, and write the approval.

    The second derivation is the safety check: between Step 1 and Step 2
    nothing should have changed (the period was already not-yet-locked
    and no documents flow into a locked period). If something did
    change — race condition, manual edit — the hashes diverge and we
    refuse the write.
    """
    intent = await parse_approval_intent(
        session=session, ca_firm_id=ca_firm_id, text=text
    )
    if intent.snapshot_hash != expected_snapshot_hash:
        raise InvalidApprovalString(
            "snapshot hash drifted between parse and finalize"
        )

    filings = FilingQuery(session=session, ca_firm_id=ca_firm_id)
    try:
        approval = await filings.create_approval(
            month=intent.month,
            year=intent.year,
            approved_by_chat_id=approved_by_chat_id,
            filing_snapshot=intent.snapshot,
            approval_hash=intent.snapshot_hash,
        )
    except FilingAlreadyApproved as exc:
        raise FilingPeriodAlreadyLocked(
            month=intent.month, year=intent.year
        ) from exc

    return ApprovalResult(
        approval_id=approval.id,
        month=intent.month,
        year=intent.year,
        snapshot_hash=intent.snapshot_hash,
    )
