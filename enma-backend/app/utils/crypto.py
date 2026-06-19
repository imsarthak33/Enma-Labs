"""Symmetric encryption for credentials stored at rest.

Used by W4 (multi-channel) to keep per-firm WhatsApp auth tokens out
of plaintext columns. Fernet is the chosen primitive:

  * AES-128-CBC + HMAC-SHA256 + PKCS7, ciphertext is URL-safe base64.
  * Versioned format (byte ``0x80`` prefix) — safe to swap algorithms
    via :class:`cryptography.fernet.MultiFernet` later without
    rewriting at-rest blobs.
  * Synchronous and dependency-free of AWS at runtime; KMS-wrapped
    Fernet (Fernet key encrypted with KMS, decrypted once at boot) is
    the W5+ hardening path with NO schema change required.

Key management
--------------
The Fernet key lives in env ``ENMA_CRYPTO_KEY``, generated once via::

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

…and added to the prod task def alongside the other secrets. Tests
get a generated key from the :func:`_get_fernet` lazy lookup.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Final

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

__all__ = [
    "CryptoError",
    "decrypt_token",
    "encrypt_token",
]


class CryptoError(RuntimeError):
    """Raised when encryption or decryption fails."""


_KEY_SETTING_NAME: Final[str] = "enma_crypto_key"


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    """Return the process-wide Fernet instance, built from settings.

    Cached so we read settings once. Tests override by clearing the
    cache (``_get_fernet.cache_clear()``) after monkeypatching.
    """
    raw = getattr(settings, _KEY_SETTING_NAME, None)
    if raw is None:
        raise CryptoError(
            "ENMA_CRYPTO_KEY is not configured. Generate one with "
            "Fernet.generate_key() and set it in the task def env."
        )
    # ``raw`` may be a pydantic SecretStr or a plain str — accept both.
    key = raw.get_secret_value() if hasattr(raw, "get_secret_value") else str(raw)
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise CryptoError(f"ENMA_CRYPTO_KEY is not a valid Fernet key: {exc}") from exc


def encrypt_token(plaintext: str) -> bytes:
    """Encrypt a credential string for at-rest storage.

    Returns raw bytes (Fernet's URL-safe base64 output, decoded). The
    caller persists these in a ``BYTEA`` column. ``plaintext`` MUST
    be non-empty — encrypting an empty string would round-trip but
    is almost always a caller bug.
    """
    if not plaintext:
        raise CryptoError("refusing to encrypt empty plaintext")
    return _get_fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_token(ciphertext: bytes) -> str:
    """Decrypt a credential blob written by :func:`encrypt_token`.

    Raises :class:`CryptoError` on tampering, wrong key, or corrupt
    input — never returns garbage.
    """
    if not ciphertext:
        raise CryptoError("refusing to decrypt empty ciphertext")
    try:
        return _get_fernet().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise CryptoError("decrypt_token: invalid or tampered ciphertext") from exc
