"""Decimal arithmetic helpers — the single place money math is allowed.

Rules of the road:

* All monetary values are :class:`decimal.Decimal`. Never ``float``.
* All extraction values arriving as strings or numbers from the LLM go
  through :func:`parse_money` so a malformed value is caught at the edge.
* Tax computations quantize at two decimal places using ``ROUND_HALF_UP``
  (the convention CAs use; matches GSTN portal behaviour).

Why two helpers and not just ``Decimal(x)`` directly?

* The LLM frequently emits values like ``"₹ 1,23,456.78"`` or ``"1234.567"``
  (extra precision). We strip currency markers, commas, and clamp to a
  fixed 2-dp representation here so downstream code can assume a clean
  quantum.
* A central parser lets us add Sentry breadcrumbs the day we see weird
  inputs in production.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final

__all__ = [
    "MONEY_QUANTUM",
    "TWO_PLACES",
    "ZERO",
    "parse_money",
    "quantize_money",
    "sum_money",
]


ZERO: Final[Decimal] = Decimal("0")

# Standard money quantum: 2 decimal places.
TWO_PLACES: Final[Decimal] = Decimal("0.01")
MONEY_QUANTUM: Final[Decimal] = TWO_PLACES  # alias for readability

# Match the first numeric token in a string: optional leading sign, one
# or more digits possibly grouped with commas (Indian or Western), optional
# decimal portion. We deliberately do NOT just strip "non-numeric" chars,
# because then a literal "Rs." or "Mr." would leak the embedded dot into
# the value and turn "Rs. 100" into "0.100". Matching the explicit numeric
# shape is the safe move.
_MONEY_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def parse_money(value: object) -> Decimal:
    """Coerce ``value`` to a 2-dp :class:`Decimal`.

    Accepts:
        * ``Decimal`` — quantised and returned.
        * ``int`` — converted to ``Decimal`` exactly.
        * ``str`` — currency symbols, commas, and whitespace stripped,
          then parsed.
        * ``float`` — REJECTED. Float-to-decimal hides binary representation
          errors that we never want in a tax computation. Cast at the call
          site if you genuinely have one.

    Raises:
        TypeError: when ``value`` is a float or an unsupported type.
        ValueError: when ``value`` is a string that doesn't parse.
    """
    if isinstance(value, bool):  # bool is an int — reject explicitly.
        raise TypeError(f"refusing to parse bool as money: {value!r}")
    if isinstance(value, Decimal):
        return quantize_money(value)
    if isinstance(value, int):
        return quantize_money(Decimal(value))
    if isinstance(value, float):
        raise TypeError(
            f"refusing to parse float as money ({value!r}); " "cast at the call site if intentional"
        )
    if isinstance(value, str):
        match = _MONEY_TOKEN_RE.search(value)
        if match is None:
            raise ValueError(f"unparseable money string: {value!r}")
        cleaned = match.group(0).replace(",", "")
        if cleaned in ("", "-"):
            raise ValueError(f"unparseable money string: {value!r}")
        try:
            return quantize_money(Decimal(cleaned))
        except InvalidOperation as exc:
            raise ValueError(f"unparseable money string: {value!r}") from exc
    raise TypeError(f"unsupported money type: {type(value).__name__}")


def quantize_money(value: Decimal) -> Decimal:
    """Quantise ``value`` to two decimal places with ROUND_HALF_UP."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def sum_money(values: object) -> Decimal:
    """Sum an iterable of money values (already-parsed Decimals).

    Accepts any iterable yielding ``Decimal`` instances. We deliberately
    do NOT auto-parse here — callers should run :func:`parse_money` on
    each item first. Mixing parsed and unparsed values is the kind of
    subtle bug we want to fail loudly on.
    """
    total = ZERO
    for v in values:  # type: ignore[attr-defined]
        if not isinstance(v, Decimal):
            raise TypeError(f"sum_money input contains non-Decimal: {type(v).__name__}")
        total += v
    return quantize_money(total)
