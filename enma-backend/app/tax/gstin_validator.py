"""GSTIN validator — regex shape check + Mod-36 checksum.

A GSTIN is 15 characters:

    Position 1-2   : State code (numeric, 01-37 + a handful of UT codes).
    Position 3-12  : PAN (10 chars: 5 alpha, 4 digit, 1 alpha).
    Position 13    : Entity code (alphanumeric — number of registrations
                     for the same PAN within a state, 1-9 then A-Z).
    Position 14    : Default 'Z' for normal taxpayer (X for OIDAR etc.).
    Position 15    : Checksum (Mod-36 over the preceding 14 chars).

We validate shape first (cheap) and only compute the checksum on shape pass.

References
----------
- GSTIN format: CGST Act, Notification No. 21/2017.
- Mod-36 algorithm: GSTN portal validation logic (public knowledge).
- State codes: GST Council master list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

__all__ = [
    "VALID_STATE_CODES",
    "GstinValidationResult",
    "compute_checksum",
    "is_valid_gstin",
    "validate_gstin",
]


# ---------------------------------------------------------------------------
# Shape regex
# ---------------------------------------------------------------------------

# Position breakdown:
#   ^(\d{2})        state code
#   ([A-Z]{5})      PAN block 1
#   (\d{4})         PAN block 2
#   ([A-Z])         PAN block 3
#   ([0-9A-Z])      entity code
#   ([A-Z])         default 'Z' (kept flexible — some legitimate variants exist)
#   ([0-9A-Z])$     checksum char
_GSTIN_SHAPE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(\d{2})([A-Z]{5})(\d{4})([A-Z])([0-9A-Z])([A-Z])([0-9A-Z])$"
)


# ---------------------------------------------------------------------------
# Valid Indian state / UT codes
# ---------------------------------------------------------------------------

# Source: GST Council master list (https://gst.gov.in). Pinning these
# exactly catches transposed-digit typos that pass the shape regex but
# could not correspond to a real state.
VALID_STATE_CODES: Final[frozenset[str]] = frozenset(
    {
        "01",  # Jammu & Kashmir
        "02",  # Himachal Pradesh
        "03",  # Punjab
        "04",  # Chandigarh
        "05",  # Uttarakhand
        "06",  # Haryana
        "07",  # Delhi
        "08",  # Rajasthan
        "09",  # Uttar Pradesh
        "10",  # Bihar
        "11",  # Sikkim
        "12",  # Arunachal Pradesh
        "13",  # Nagaland
        "14",  # Manipur
        "15",  # Mizoram
        "16",  # Tripura
        "17",  # Meghalaya
        "18",  # Assam
        "19",  # West Bengal
        "20",  # Jharkhand
        "21",  # Odisha
        "22",  # Chhattisgarh
        "23",  # Madhya Pradesh
        "24",  # Gujarat
        "26",  # Dadra & Nagar Haveli and Daman & Diu (post-merger)
        "27",  # Maharashtra
        "29",  # Karnataka
        "30",  # Goa
        "31",  # Lakshadweep
        "32",  # Kerala
        "33",  # Tamil Nadu
        "34",  # Puducherry
        "35",  # Andaman & Nicobar Islands
        "36",  # Telangana
        "37",  # Andhra Pradesh
        "38",  # Ladakh
        "97",  # Other Territory
        "99",  # Centre Jurisdiction
    }
)


# ---------------------------------------------------------------------------
# Mod-36 checksum
# ---------------------------------------------------------------------------

# 0-9 then A-Z. Position in this string = digit value.
_BASE36: Final[str] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_BASE36_INDEX: Final[dict[str, int]] = {c: i for i, c in enumerate(_BASE36)}


def compute_checksum(first14: str) -> str:
    """Compute the GSTIN check character from the first 14 positions.

    Algorithm (well-documented; same logic used by GSTN's official
    validation utility):

      1. Map each char to its base-36 index (0-9 → 0-9, A-Z → 10-35).
      2. Multiply every other digit by 2, starting from the rightmost
         (i.e. positions are alternately weighted by [2, 1, 2, 1, ...]).
      3. For each weighted product, sum its base-36 digits (i.e.
         ``product // 36 + product % 36``).
      4. Sum every contribution.
      5. Check digit = ``(36 - sum % 36) % 36`` mapped back into base-36.

    Raises:
        ValueError: when ``first14`` is the wrong length or contains a
            character outside ``[0-9A-Z]``.
    """
    if len(first14) != 14:
        raise ValueError(f"expected 14 chars, got {len(first14)}")
    total = 0
    # Per the GSTN reference implementation: iterate from the RIGHTMOST char
    # leftward, with the factor alternating 2, 1, 2, 1, ... starting at 2.
    factor = 2
    for ch in reversed(first14):
        idx = _BASE36_INDEX.get(ch)
        if idx is None:
            raise ValueError(f"non-base36 character: {ch!r}")
        product = idx * factor
        total += (product // 36) + (product % 36)
        factor = 1 if factor == 2 else 2
    remainder = total % 36
    check_value = (36 - remainder) % 36
    return _BASE36[check_value]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GstinValidationResult:
    """Structured outcome of GSTIN validation.

    ``is_valid`` is True only when all three checks pass: shape, state,
    and checksum. ``reason`` carries the first failing check for logs;
    callers should never display it to end users.
    """

    is_valid: bool
    reason: str | None = None
    state_code: str | None = None
    pan: str | None = None


def validate_gstin(  # noqa: PLR0911 — each return is a distinct failure mode
    value: str | None,
) -> GstinValidationResult:
    """Validate a GSTIN string. Whitespace and case are normalised first."""
    if value is None or not isinstance(value, str):
        return GstinValidationResult(is_valid=False, reason="missing")
    normalised = value.strip().upper()
    if not normalised:
        return GstinValidationResult(is_valid=False, reason="empty")
    if len(normalised) != 15:
        return GstinValidationResult(
            is_valid=False,
            reason=f"wrong length ({len(normalised)} chars, expected 15)",
        )
    m = _GSTIN_SHAPE_RE.match(normalised)
    if not m:
        return GstinValidationResult(is_valid=False, reason="shape mismatch")

    state_code = m.group(1)
    pan = normalised[2:12]
    if state_code not in VALID_STATE_CODES:
        return GstinValidationResult(
            is_valid=False,
            reason=f"unknown state code {state_code!r}",
            state_code=state_code,
            pan=pan,
        )

    expected_check = compute_checksum(normalised[:14])
    if expected_check != normalised[14]:
        return GstinValidationResult(
            is_valid=False,
            reason="checksum mismatch",
            state_code=state_code,
            pan=pan,
        )

    return GstinValidationResult(is_valid=True, state_code=state_code, pan=pan)


def is_valid_gstin(value: str | None) -> bool:
    """Boolean convenience wrapper around :func:`validate_gstin`."""
    return validate_gstin(value).is_valid
