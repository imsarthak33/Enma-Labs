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

from app.db.models.firm import CaFirm, FirmUser
from app.services.messaging.base import MessageClient, MessagingError
from app.services.messaging.telegram_client import TelegramClient

__all__ = [
    "client_for",
    "resolve_channel",
]


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

    Cheap: Telegram returns a process-singleton; WhatsApp (P2.b)
    memoises per-firm so credentials decrypt once per process.
    """
    channel = resolve_channel(firm=firm, user=user)
    if channel == _TELEGRAM:
        return _telegram_singleton()
    # _WHATSAPP — P2.b wires this in.
    return _whatsapp_client_for(firm.id)


@lru_cache(maxsize=1)
def _telegram_singleton() -> TelegramClient:
    """One TelegramClient per process — it holds no per-firm state."""
    return TelegramClient()


def _whatsapp_client_for(firm_id: uuid.UUID) -> MessageClient:
    """Stub — P2.b implements the Twilio-backed WhatsApp client."""
    raise MessagingError(
        f"WhatsApp client not yet wired for firm {firm_id}. "
        "Land P2.b before routing this firm to WhatsApp."
    )
