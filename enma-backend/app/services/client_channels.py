"""Per-client channel helpers (ADR-017).

The two deterministic channels a client reaches Enma on — no groups:

* the **1:1 Telegram deep link** (chase + interactive drop): tapping
  ``t.me/<bot>?start=client_<uuid>`` binds that chat to the client, and
* the **per-client email-ingest address** (passive / auto-forward).

Both embed the client UUID, so routing is deterministic. The CA hands these
to a client once; thereafter documents flow in and chases go out on them.
"""

from __future__ import annotations

import uuid

from app.config import settings
from app.services.providers.email_ingest import ingest_address_for_client

__all__ = [
    "CLIENT_LINK_PREFIX",
    "client_deep_link",
    "client_ingest_email",
    "parse_client_link_payload",
]

# ``/start`` deep-link payload prefix that marks a client-binding link (vs a
# firm-onboarding UUID). Underscore (not hyphen) — Telegram deep-link payloads
# allow ``A-Za-z0-9_-`` but we keep it simple and unambiguous.
CLIENT_LINK_PREFIX = "client_"


def client_deep_link(client_id: uuid.UUID) -> str:
    """The per-client Telegram deep link the CA sends to a client."""
    return (
        f"https://t.me/{settings.telegram_bot_username}"
        f"?start={CLIENT_LINK_PREFIX}{client_id}"
    )


def client_ingest_email(client_id: uuid.UUID) -> str:
    """The per-client email address a client forwards bank statements to."""
    return ingest_address_for_client(client_id, domain=settings.email_ingest_domain)


def parse_client_link_payload(payload: str) -> uuid.UUID | None:
    """Extract the client UUID from a ``/start`` payload, or ``None``.

    Returns ``None`` for a non-client payload (a firm-onboarding UUID, or
    junk) so the caller can fall through to the firm-link path.
    """
    if not payload.startswith(CLIENT_LINK_PREFIX):
        return None
    try:
        return uuid.UUID(payload[len(CLIENT_LINK_PREFIX) :])
    except ValueError:
        return None
