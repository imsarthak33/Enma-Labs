"""Tests for the Fernet credential encryption helper.

The helper is the load-bearing primitive for W4 multi-channel: every
per-firm WhatsApp auth_token round-trips through encrypt_token /
decrypt_token. These tests lock the contract.
"""

from __future__ import annotations

import pytest
from app.utils.crypto import CryptoError, _get_fernet, decrypt_token, encrypt_token
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def _reset_fernet_cache():
    """Tests may monkeypatch the key; clear the lru_cache between them."""
    _get_fernet.cache_clear()
    yield
    _get_fernet.cache_clear()


def test_round_trip_preserves_plaintext() -> None:
    plaintext = "twilio_auth_token_abc123_xyz789"
    blob = encrypt_token(plaintext)
    assert isinstance(blob, bytes)
    assert decrypt_token(blob) == plaintext


def test_round_trip_unicode() -> None:
    """Unicode auth tokens (rare but possible) must survive."""
    plaintext = "tøken_with_ünïcødé_₹100"
    assert decrypt_token(encrypt_token(plaintext)) == plaintext


def test_ciphertext_is_not_plaintext() -> None:
    """Sanity: the encrypted blob does not contain the plaintext."""
    plaintext = "very_secret_token"
    blob = encrypt_token(plaintext)
    assert plaintext.encode() not in blob


def test_two_encrypts_differ() -> None:
    """Fernet IV makes ciphertexts distinct even for identical inputs."""
    a = encrypt_token("same_plaintext")
    b = encrypt_token("same_plaintext")
    assert a != b
    # Both still decrypt back to the same value.
    assert decrypt_token(a) == decrypt_token(b) == "same_plaintext"


def test_empty_plaintext_refused() -> None:
    with pytest.raises(CryptoError, match="empty plaintext"):
        encrypt_token("")


def test_empty_ciphertext_refused() -> None:
    with pytest.raises(CryptoError, match="empty ciphertext"):
        decrypt_token(b"")


def test_tampered_ciphertext_raises() -> None:
    """A single-byte flip in the middle of the blob breaks the HMAC."""
    blob = bytearray(encrypt_token("real_token"))
    # Flip a byte near the end (past the version + IV) so we hit the HMAC.
    blob[-5] ^= 0x01
    with pytest.raises(CryptoError, match="invalid or tampered"):
        decrypt_token(bytes(blob))


def test_decrypt_with_wrong_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cipher encrypted under one key must NOT decrypt under another."""
    # Encrypt under the default test key.
    blob = encrypt_token("token_under_key_a")
    # Swap to a different key and attempt decrypt.
    new_key = Fernet.generate_key().decode()
    monkeypatch.setattr("app.utils.crypto.settings.enma_crypto_key", _FakeSecret(new_key))
    _get_fernet.cache_clear()
    with pytest.raises(CryptoError, match="invalid or tampered"):
        decrypt_token(blob)


class _FakeSecret:
    """Minimal SecretStr stand-in for monkeypatched key swap."""

    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value
