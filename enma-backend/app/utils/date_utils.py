"""IST timezone + filing-period helpers — the only place we reason about dates.

Indian compliance work is IST-anchored. Doing date math in UTC and
converting at the edges is a recipe for off-by-one-day bugs around
filing deadlines (GSTR-1 11th, GSTR-3B 20th). This module centralises:

* :data:`IST` — the Asia/Kolkata zone (UTC+05:30, no DST).
* :func:`now_ist` — current IST datetime; the engine-version clock.
* :class:`FilingPeriod` — a ``(month, year)`` tuple with arithmetic and
  containment checks.
* :func:`section_16_4_cutoff` — Section 16(4) cutoff date for an
  invoice (30 November of the FY following the invoice's FY). Past this
  cutoff the ITC is permanently barred.

No floats, no naive datetimes, no string parsing without an explicit
format. All public functions are pure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final
from zoneinfo import ZoneInfo

__all__ = [
    "IST",
    "FY_END_MONTH",
    "FY_START_MONTH",
    "FilingPeriod",
    "fiscal_year_for_date",
    "now_ist",
    "parse_iso_date",
    "section_16_4_cutoff",
]


IST: Final[ZoneInfo] = ZoneInfo("Asia/Kolkata")

# Indian fiscal year runs April (4) through March (3) of next year.
FY_START_MONTH: Final[int] = 4
FY_END_MONTH: Final[int] = 3


def now_ist() -> datetime:
    """Return the current IST-aware datetime.

    Wrapped so tests can monkeypatch one symbol instead of chasing
    ``datetime.now`` across modules.
    """
    return datetime.now(tz=UTC).astimezone(IST)


def parse_iso_date(value: str | None) -> date | None:
    """Parse a strict ``YYYY-MM-DD`` string. Returns ``None`` on any failure.

    The extractor prompt mandates ISO format; this is the parser that
    enforces it. We do NOT accept ``YYYY/MM/DD``, ``DD-MM-YYYY``, or any
    other shape — silent reinterpretation of dates would be catastrophic
    in a tax context.
    """
    if value is None or not isinstance(value, str):
        return None
    stripped = value.strip()
    if len(stripped) != 10:
        return None
    try:
        return date.fromisoformat(stripped)
    except ValueError:
        return None


@dataclass(frozen=True, order=True)
class FilingPeriod:
    """A monthly GST filing period.

    Ordering is calendar-natural: ``FilingPeriod(3, 2026) > FilingPeriod(12, 2025)``.
    The frozen-dataclass ``order=True`` derives comparisons from the field
    declaration order — keep ``year`` first.
    """

    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise ValueError(f"month out of range: {self.month}")
        if self.year < 2000 or self.year > 2100:
            raise ValueError(f"year out of range: {self.year}")

    @classmethod
    def from_date(cls, d: date) -> FilingPeriod:
        return cls(year=d.year, month=d.month)

    def as_tuple(self) -> tuple[int, int]:
        """Canonical ``(month, year)`` representation used as a dict key.

        Note the order: we keep the public tuple shape ``(month, year)``
        because that matches the existing ``documents.filing_period_month``
        / ``filing_period_year`` column ordering on the database side, even
        though the dataclass orders by ``year`` first for natural sort.
        """
        return (self.month, self.year)


def fiscal_year_for_date(d: date) -> int:
    """Return the Indian fiscal-year *start* year for ``d``.

    Example: 2025-01-15 → FY 2024-25 → returns ``2024``.
              2025-04-01 → FY 2025-26 → returns ``2025``.
    """
    if d.month >= FY_START_MONTH:
        return d.year
    return d.year - 1


def section_16_4_cutoff(invoice_date: date) -> date:
    """Return the Section 16(4) ITC cutoff date for ``invoice_date``.

    Section 16(4) of the CGST Act bars ITC after the earlier of:
      (a) 30 November of the FY following the invoice's FY, OR
      (b) date of filing of the annual return.

    We use (a) as the deterministic, conservative cutoff. The engine
    compares ``current_date`` against this and reclassifies the ITC as
    ``block_amount`` with reason ``time_barred`` past the cutoff.
    """
    invoice_fy_start = fiscal_year_for_date(invoice_date)
    # FY 2024-25 ends 31 Mar 2025 → next FY is 2025-26 → cutoff is 30 Nov 2025.
    cutoff_calendar_year = invoice_fy_start + 1
    return date(cutoff_calendar_year, 11, 30)
