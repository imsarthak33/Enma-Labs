"""Tests for ``app.utils.hash_utils``."""

from __future__ import annotations

from app.utils.hash_utils import sha256_canonical_json, sha256_hex


class TestSha256Hex:
    def test_known_vector(self) -> None:
        # SHA-256("abc") — RFC 4634.
        assert (
            sha256_hex(b"abc")
            == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )

    def test_empty_bytes(self) -> None:
        assert (
            sha256_hex(b"")
            == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )


class TestCanonicalJson:
    def test_key_order_invariant(self) -> None:
        a = sha256_canonical_json({"a": 1, "b": 2})
        b = sha256_canonical_json({"b": 2, "a": 1})
        assert a == b

    def test_nested_order_invariant(self) -> None:
        a = sha256_canonical_json({"outer": {"x": 1, "y": 2}})
        b = sha256_canonical_json({"outer": {"y": 2, "x": 1}})
        assert a == b

    def test_value_change_produces_different_hash(self) -> None:
        a = sha256_canonical_json({"k": 1})
        b = sha256_canonical_json({"k": 2})
        assert a != b

    def test_non_serialisable_falls_back_to_str(self) -> None:
        # No exception thrown — default=str handles UUID / Decimal etc.
        import uuid

        h = sha256_canonical_json({"id": uuid.UUID(int=0)})
        assert isinstance(h, str)
        assert len(h) == 64
