"""Tests for IST + filing-period helpers."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from app.utils.date_utils import (
    IST,
    FilingPeriod,
    fiscal_year_for_date,
    now_ist,
    parse_iso_date,
    section_16_4_cutoff,
)


class TestNowIst:
    def test_returns_ist_aware_datetime(self) -> None:
        ts = now_ist()
        assert ts.tzinfo is not None
        # IST offset is +05:30 — no DST.
        assert ts.utcoffset() == timedelta(hours=5, minutes=30)

    def test_matches_utc_within_seconds(self) -> None:
        ts = now_ist()
        utc_now = datetime.now(tz=UTC)
        delta = abs((ts.astimezone(UTC) - utc_now).total_seconds())
        assert delta < 5


class TestParseIsoDate:
    def test_valid(self) -> None:
        assert parse_iso_date("2025-03-15") == date(2025, 3, 15)

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "  ",
            "2025/03/15",
            "15-03-2025",
            "2025-3-15",
            "not-a-date",
            "2025-13-01",
        ],
    )
    def test_invalid_returns_none(self, value: object) -> None:
        assert parse_iso_date(value) is None  # type: ignore[arg-type]


class TestFilingPeriod:
    def test_construct_and_tuple(self) -> None:
        fp = FilingPeriod(year=2025, month=3)
        assert fp.as_tuple() == (3, 2025)

    def test_natural_order(self) -> None:
        assert FilingPeriod(2025, 12) < FilingPeriod(2026, 1)
        assert FilingPeriod(2026, 3) > FilingPeriod(2025, 12)

    def test_from_date(self) -> None:
        fp = FilingPeriod.from_date(date(2025, 6, 15))
        assert fp == FilingPeriod(year=2025, month=6)

    @pytest.mark.parametrize("month", [0, 13, -1, 100])
    def test_invalid_month(self, month: int) -> None:
        with pytest.raises(ValueError, match="month"):
            FilingPeriod(year=2025, month=month)

    @pytest.mark.parametrize("year", [1999, 2101, 0])
    def test_invalid_year(self, year: int) -> None:
        with pytest.raises(ValueError, match="year"):
            FilingPeriod(year=year, month=6)


class TestFiscalYearForDate:
    @pytest.mark.parametrize(
        ("d", "fy_start"),
        [
            (date(2024, 4, 1), 2024),
            (date(2024, 12, 31), 2024),
            (date(2025, 3, 31), 2024),
            (date(2025, 4, 1), 2025),
            (date(2025, 1, 15), 2024),
        ],
    )
    def test_indian_fy_boundary(self, d: date, fy_start: int) -> None:
        assert fiscal_year_for_date(d) == fy_start


class TestSection164Cutoff:
    @pytest.mark.parametrize(
        ("invoice_date", "expected_cutoff"),
        [
            # FY 2024-25 invoice → cutoff 30 Nov 2025.
            (date(2024, 4, 15), date(2025, 11, 30)),
            (date(2025, 3, 31), date(2025, 11, 30)),
            # FY 2025-26 invoice → cutoff 30 Nov 2026.
            (date(2025, 4, 1), date(2026, 11, 30)),
            (date(2026, 1, 10), date(2026, 11, 30)),
        ],
    )
    def test_cutoff(self, invoice_date: date, expected_cutoff: date) -> None:
        assert section_16_4_cutoff(invoice_date) == expected_cutoff


def test_ist_is_kolkata() -> None:
    """Sanity: the IST constant is Asia/Kolkata, not a random fixed offset."""
    assert str(IST) == "Asia/Kolkata"
