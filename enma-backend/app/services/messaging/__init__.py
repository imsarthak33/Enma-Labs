"""Multi-channel messaging — abstract surface for Telegram + WhatsApp.

Single import surface for the rest of the app::

    from app.services.messaging import factory, MessageClient, Markup

The factory resolves a ``CaFirm`` (and optionally a ``FirmUser``) to
the concrete ``MessageClient`` to use. Call sites NEVER instantiate
channel-specific clients directly — that locks the abstraction.
"""

from app.services.messaging.base import (
    Markup,
    MessageClient,
    MessagingError,
)

__all__ = [
    "Markup",
    "MessageClient",
    "MessagingError",
]
