"""Resolve a firm (+ optional user) to its configured ``MessageClient``.

Decision tree
-------------
    user.primary_channel   →   firm.primary_channel   →   client class
    ─────────────────────────────────────────────────────────────────
    'telegram'             →   (irrelevant)           →   TelegramClient
    'whatsapp'             →   (irrelevant)           →   WhatsAppClient
    None  (inherit)        →   'telegram' (default)   →   TelegramClient
    None  (inherit)        →   'whatsapp'             →   WhatsAppClient

The decrypted WhatsApp credentials are looked up once per process per
firm — a small in-process cache keeps Fernet decrypts off the hot path
without inviting cross-firm leakage (each firm's bytes live under its
own UUID key).

Concrete WhatsApp client wiring lands in P2.b; for now the factory
raises a clear :class:`MessagingError` when a firm asks for WhatsApp
without the client being available.
"""

from __future__ import annotations

import uuid
from functools import lru_cache
from threading import Lock

from app.db.models.firm import CaFirm, FirmUser
from app.services.messaging.base import MessageClient, MessagingError
from app.services.messaging.telegram_client import TelegramClient
from app.services.messaging.whatsapp_client import WhatsAppClient
from app.utils.crypto import CryptoError, decrypt_token

__all__ = [
    "client_for",
    "reset_whatsapp_cache",
    "resolve_channel",
]


# ---------------------------------------------------------------------------
# Per-firm WhatsApp client cache
#
# Decrypting a Fernet token is ~10 microseconds, but credential
# decoding shouldn't be in the hot path of every send. Cache the
# constructed WhatsAppClient instance per firm UUID. Thread-safe via
# a tiny Lock — the FastAPI workers may import this from multiple
# event loops if scaled, and we'd rather pay one mutex than risk a
# torn write to the dict.
# ---------------------------------------------------------------------------


_wa_cache: dict[uuid.UUID, WhatsAppClient] = {}
_wa_cache_lock: Lock = Lock()


def reset_whatsapp_cache() -> None:
    """Drop all cached WhatsApp clients — used by tests + after key rotation."""
    with _wa_cache_lock:
        _wa_cache.clear()


_TELEGRAM: str = "telegram"
_WHATSAPP: str = "whatsapp"


def resolve_channel(*, firm: CaFirm, user: FirmUser | None = None) -> str:
    """Pick the channel string ('telegram' | 'whatsapp') for this call.

    User-level override wins; otherwise inherit the firm default.
    Unknown values fall back to ``'telegram'`` so a corrupted DB row
    never crashes the worker.
    """
    if user is not None and user.primary_channel:
        candidate = user.primary_channel
    else:
        candidate = firm.primary_channel or _TELEGRAM
    if candidate not in (_TELEGRAM, _WHATSAPP):
        return _TELEGRAM
    return candidate


def client_for(*, firm: CaFirm, user: FirmUser | None = None) -> MessageClient:
    """Return the configured :class:`MessageClient` for this firm/user.

    Telegram returns a process-singleton (no per-firm state).
    WhatsApp memoises per-firm so the Fernet decrypt only runs once
    per process per firm.
    """
    channel = resolve_channel(firm=firm, user=user)
    if channel == _TELEGRAM:
        return _telegram_singleton()
    return _whatsapp_client_for(firm)


@lru_cache(maxsize=1)
def _telegram_singleton() -> TelegramClient:
    """One TelegramClient per process — it holds no per-firm state."""
    return TelegramClient()


def _whatsapp_client_for(firm: CaFirm) -> WhatsAppClient:
    """Materialise (or fetch from cache) the per-firm WhatsApp client.

    Validates required credentials are present in the row + decrypts
    the auth_token via :func:`app.utils.crypto.decrypt_token`. Any
    failure surfaces as :class:`MessagingError` so callers handle
    both channels' setup gaps the same way.
    """
    cached = _wa_cache.get(firm.id)
    if cached is not None:
        return cached

    if firm.whatsapp_provider not in ("twilio",):
        raise MessagingError(
            f"WhatsApp client: firm {firm.id} has unsupported provider "
            f"{firm.whatsapp_provider!r}. Only 'twilio' is wired today; "
            f"'meta_cloud' is a W5+ swap."
        )
    if not firm.whatsapp_account_id:
        raise MessagingError(
            f"WhatsApp client: firm {firm.id} has no whatsapp_account_id "
            f"configured (Twilio account_sid)."
        )
    if not firm.whatsapp_phone_number:
        raise MessagingError(
            f"WhatsApp client: firm {firm.id} has no whatsapp_phone_number "
            f"(sender / sandbox number)."
        )
    if firm.whatsapp_auth_token_encrypted is None:
        raise MessagingError(
            f"WhatsApp client: firm {firm.id} has no encrypted auth_token. "
            f"Re-run the WhatsApp onboarding flow."
        )

    try:
        auth_token = decrypt_token(firm.whatsapp_auth_token_encrypted)
    except CryptoError as exc:
        raise MessagingError(
            f"WhatsApp client: failed to decrypt auth_token for firm "
            f"{firm.id} — {exc}"
        ) from exc

    client = WhatsAppClient(
        account_sid=firm.whatsapp_account_id,
        auth_token=auth_token,
        from_number=firm.whatsapp_phone_number,
    )
    with _wa_cache_lock:
        # Re-check inside the lock in case another worker raced.
        existing = _wa_cache.get(firm.id)
        if existing is not None:
            return existing
        _wa_cache[firm.id] = client
    return client
