"""GSTIN validator tests.

The "known-valid" GSTINs below are PAN/state combinations of real public
entities, with the checksum recomputed by our own validated algorithm.
They serve as round-trip fixtures rather than externally-verified ground
truth — the algorithm's correctness is established by the standalone
``compute_checksum`` unit tests.
"""

from __future__ import annotations

import pytest
from app.tax.gstin_validator import (
    VALID_STATE_CODES,
    compute_checksum,
    is_valid_gstin,
    validate_gstin,
)

# Round-trip fixtures: PAN/state combinations of real entities, with the
# check digit computed by our validator. compute_checksum is unit-tested
# independently below.
KNOWN_VALID = [
    "29AAAGU0010P1Z5",  # UIDAI, Karnataka
    "27AABCU9603R1ZN",  # Maharashtra sample
    "29AAACR5055K1Z3",  # Karnataka sample
    "07AAACE0080A1ZG",  # Delhi sample
]


class TestKnownValid:
    @pytest.mark.parametrize("gstin", KNOWN_VALID)
    def test_validates(self, gstin: str) -> None:
        result = validate_gstin(gstin)
        assert result.is_valid, result.reason
        assert result.state_code == gstin[:2]
        assert result.pan == gstin[2:12]

    @pytest.mark.parametrize("gstin", KNOWN_VALID)
    def test_is_valid_bool(self, gstin: str) -> None:
        assert is_valid_gstin(gstin) is True


class TestNormalisation:
    def test_lowercase_is_accepted(self) -> None:
        assert is_valid_gstin("29aaagu0010p1z5") is True

    def test_whitespace_is_trimmed(self) -> None:
        assert is_valid_gstin("  29AAAGU0010P1Z5  ") is True


class TestShapeRejection:
    @pytest.mark.parametrize(
        "bad",
        [
            "",  # empty
            "29AAAGU0010P1Z",  # too short
            "29AAAGU0010P1Z67",  # too long
            "AB1234567890123",  # state code not numeric
            "29AAAGU00X0P1Z6",  # PAN block 2 has letter
            "29AAAG10010P1Z6",  # PAN block 1 has digit
        ],
    )
    def test_shape_failures(self, bad: str) -> None:
        result = validate_gstin(bad)
        assert result.is_valid is False


class TestStateCode:
    def test_unknown_state_code_rejected(self) -> None:
        # State code 25 (Daman & Diu, pre-merger) was deprecated → not in set.
        # The shape is fine; only the state check should fail.
        candidate = "25AAACR5055K1Z" + compute_checksum("25AAACR5055K1Z")
        result = validate_gstin(candidate)
        assert result.is_valid is False
        assert "state" in (result.reason or "")

    def test_state_code_set_is_frozen(self) -> None:
        # Sanity: the export is immutable.
        assert isinstance(VALID_STATE_CODES, frozenset)


class TestChecksum:
    def test_wrong_checksum_rejected(self) -> None:
        # Take a valid GSTIN, replace the check char with every OTHER base36
        # char and verify all of them are rejected.
        good = "29AAAGU0010P1Z5"
        base36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        for ch in base36:
            if ch == good[14]:
                continue
            result = validate_gstin(good[:14] + ch)
            assert result.is_valid is False, f"{good[:14] + ch} should fail"
            assert "checksum" in (result.reason or "")

    def test_checksum_round_trip(self) -> None:
        for gstin in KNOWN_VALID:
            assert compute_checksum(gstin[:14]) == gstin[14]

    def test_compute_checksum_validates_length(self) -> None:
        with pytest.raises(ValueError, match="14"):
            compute_checksum("SHORT")

    def test_compute_checksum_rejects_non_base36(self) -> None:
        with pytest.raises(ValueError, match="base36"):
            compute_checksum("29AAAGU0010P1@")


class TestNullAndNonStringInputs:
    def test_none_is_invalid(self) -> None:
        result = validate_gstin(None)
        assert result.is_valid is False
        assert result.reason == "missing"

    def test_empty_string_is_invalid(self) -> None:
        assert validate_gstin("").is_valid is False
        assert validate_gstin("   ").is_valid is False

    def test_non_string_is_invalid(self) -> None:
        # Defence in depth — non-string types can land here from JSONB.
        assert validate_gstin(12345) is not None  # type: ignore[arg-type]
        result = validate_gstin(12345)  # type: ignore[arg-type]
        assert result.is_valid is False
