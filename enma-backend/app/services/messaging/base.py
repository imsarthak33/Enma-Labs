"""Abstract messaging client + channel-agnostic markup AST.

Why these two things live together
----------------------------------
The two concerns are intertwined: the client is the *destination* and
the markup AST is the *content*. The renderer for the markup lives on
the client (each subclass knows how to flatten the AST to its
channel's native wire format — HTML for Telegram, Twilio's WhatsApp
markup for WA). Co-locating the contract makes mismatches impossible
at type-check time.

What's intentionally NOT here
-----------------------------
* Concrete clients (live in ``telegram_client.py`` / ``whatsapp_client.py``).
* Factory / dispatch logic (``factory.py``).
* Anything tied to HTTP, Twilio's SDK, or Telegram's bot API.

This module is import-cheap and side-effect-free so every call site
in the codebase can ``from app.services.messaging import Markup``
without dragging in Twilio's REST client.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Bold",
    "Code",
    "CodeBlock",
    "Concat",
    "Italic",
    "Markup",
    "MessageClient",
    "MessagingError",
    "RawHtml",
    "Text",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MessagingError(RuntimeError):
    """Raised when a messaging operation fails irrecoverably.

    Subclassed by channel-specific transport errors. The supervisor /
    worker layers catch the base class so failures from either channel
    are handled uniformly.
    """


# ---------------------------------------------------------------------------
# Markup AST
#
# Replaces the HTML-string-everywhere convention. Old code wrote things
# like ``f"<b>{name}</b>"`` directly into a string. Now ``Bold("name")``
# is a structural node that the destination client renders correctly:
#
#   Telegram   →  "<b>name</b>"
#   WhatsApp   →  "*name*"
#
# Concat composes nodes. Most call sites build a Concat of mixed text
# and styled children and pass that to ``client.send_message``.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Text:
    """Plain text — escaped at render time per the destination channel."""

    value: str


@dataclass(frozen=True)
class Bold:
    """Bold child. Renders as ``<b>x</b>`` (TG) or ``*x*`` (WA)."""

    child: Markup


@dataclass(frozen=True)
class Italic:
    """Italic child. Renders as ``<i>x</i>`` (TG) or ``_x_`` (WA)."""

    child: Markup


@dataclass(frozen=True)
class Code:
    """Inline code. Renders as ``<code>x</code>`` (TG) or `` `x` `` (WA)."""

    child: Markup


@dataclass(frozen=True)
class CodeBlock:
    """Multi-line code block. Renders as ``<pre>x</pre>`` (TG) or
    ``` ``` ``` (WA). Used for the snapshot hashes and JSON-shaped
    blobs the user pastes back."""

    child: Markup


@dataclass(frozen=True)
class RawHtml:
    """Escape hatch for Telegram-only HTML the renderer copies verbatim.

    WhatsApp's renderer will *strip* the tags and emit the inner text
    only — this lets call sites use Telegram-specific features (like
    spoiler) without breaking WA. Use sparingly.
    """

    html: str


@dataclass(frozen=True)
class Concat:
    """Sequential composition of children."""

    children: tuple[Markup, ...] = field(default_factory=tuple)


# ``Markup`` is the union of all node types. Kept as a string forward
# ref so the dataclasses above can reference each other without import
# cycles. Use ``Markup`` everywhere a styled string used to live.
Markup = Text | Bold | Italic | Code | CodeBlock | RawHtml | Concat


# ---------------------------------------------------------------------------
# Abstract client
# ---------------------------------------------------------------------------


class MessageClient(ABC):
    """Channel-agnostic messaging surface.

    Subclasses implement the four async methods below against their
    channel's native API. The factory hands a configured instance to
    the caller; call sites never see channel-specific types.

    Recipient addresses
    -------------------
    The ``recipient`` parameter is channel-typed:

      * Telegram → ``int`` (chat_id)
      * WhatsApp → ``str`` (E.164 phone number)

    The factory hands the right type to the right client; the caller
    just passes ``firm.address_for_channel(user)``-shaped values.
    """

    # -- text --------------------------------------------------------------

    @abstractmethod
    async def send_message(
        self,
        *,
        recipient: int | str,
        body: Markup,
        reply_to_id: int | str | None = None,
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        """Send a styled text message. Returns the channel's response dict."""

    # -- documents ---------------------------------------------------------

    @abstractmethod
    async def send_document(
        self,
        *,
        recipient: int | str,
        file_bytes: bytes,
        filename: str,
        caption: Markup | None = None,
    ) -> dict[str, Any]:
        """Upload a document and post it to the recipient."""

    # -- inbound media -----------------------------------------------------

    @abstractmethod
    async def download_file(self, file_ref: str) -> bytes:
        """Download a file the channel previously delivered.

        ``file_ref`` is the channel's native identifier — Telegram's
        ``file_id`` or Twilio's media URL. The factory normalises
        inbound webhook payloads so call sites use whichever the
        channel surfaced.
        """
