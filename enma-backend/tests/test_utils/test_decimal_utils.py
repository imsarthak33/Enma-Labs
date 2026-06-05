"""Tests for the decimal money helpers."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.utils.decimal_utils import (
    TWO_PLACES,
    ZERO,
    parse_money,
    quantize_money,
    sum_money,
)


class TestParseMoney:
    def test_decimal_passes_through(self) -> None:
        assert parse_money(Decimal("100.00")) == Decimal("100.00")

    def test_int_is_quantised(self) -> None:
        assert parse_money(100) == Decimal("100.00")

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("100", Decimal("100.00")),
            ("100.5", Decimal("100.50")),
            ("100.567", Decimal("100.57")),  # ROUND_HALF_UP
            ("1,23,456.78", Decimal("123456.78")),  # Indian grouping
            ("1,234,567.89", Decimal("1234567.89")),  # Western grouping
            ("₹ 99,999.99", Decimal("99999.99")),
            ("Rs. 1,00,000", Decimal("100000.00")),
            ("-50.25", Decimal("-50.25")),
        ],
    )
    def test_string_parses(self, raw: str, expected: Decimal) -> None:
        assert parse_money(raw) == expected

    def test_float_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="float"):
            parse_money(100.5)

    def test_bool_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="bool"):
            parse_money(True)

    def test_unparseable_string_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_money("not a number")

    def test_only_currency_marker_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_money("Rs.")

    def test_unsupported_type_raises(self) -> None:
        with pytest.raises(TypeError):
            parse_money(object())


class TestQuantize:
    def test_rounds_half_up(self) -> None:
        assert quantize_money(Decimal("0.125")) == Decimal("0.13")

    def test_keeps_two_places(self) -> None:
        assert quantize_money(Decimal("100")) == Decimal("100.00")


class TestSumMoney:
    def test_sum_quantises(self) -> None:
        values = [Decimal("100.00"), Decimal("200.50"), Decimal("0.01")]
        assert sum_money(values) == Decimal("300.51")

    def test_empty_iterable_is_zero(self) -> None:
        assert sum_money([]) == ZERO

    def test_rejects_non_decimal_items(self) -> None:
        with pytest.raises(TypeError, match="non-Decimal"):
            sum_money([Decimal("1.00"), "2.00"])  # mixed input


class TestConstants:
    def test_two_places_is_canonical_quantum(self) -> None:
        assert Decimal("0.01") == TWO_PLACES

    def test_zero_is_decimal(self) -> None:
        assert isinstance(ZERO, Decimal)
        assert Decimal("0") == ZERO
