# ruff: noqa: RUF002 — docstring intentionally uses NBSP for WA markup discussion
"""Markup AST → channel-native wire format.

Two pure functions, both take the same :class:`Markup` and emit a
``str`` ready for the channel's send API. They're kept here (not on
the client classes) so they're easy to unit-test without spinning up
a real HTTP transport.

Telegram (``render_html``)
--------------------------
Telegram's HTML parse mode supports a narrow whitelist: ``<b>``,
``<i>``, ``<code>``, ``<pre>``, ``<a>``, ``<u>``, ``<s>``,
``<tg-spoiler>``. The only escape it requires inside text content is
``& < >`` — quotes and apostrophes do NOT need to be escaped (and
escaping them BREAKS the parse mode; see the W2 incident logged in
:mod:`app.formatting.telegram_html`).

WhatsApp (``render_whatsapp``)
------------------------------
WhatsApp wire format is much terser:

  * ``*x*`` — bold
  * ``_x_`` — italic
  * `` `x` `` — inline code
  * `` ``-fenced code block (three backticks on their own lines)
  * No links inside text — bare URLs are auto-linked by the client
  * No HTML escapes needed; the wire format is plain UTF-8

The renderer collapses ``RawHtml`` nodes by emitting their inner
text only (best-effort) — Telegram-only embellishments degrade
gracefully on WhatsApp.
"""

from __future__ import annotations

import html as html_module
import re
from typing import Final

from app.services.messaging.base import (
    Bold,
    Code,
    CodeBlock,
    Concat,
    Italic,
    Markup,
    RawHtml,
    Text,
)

__all__ = [
    "render_html",
    "render_whatsapp",
]


# ---------------------------------------------------------------------------
# Telegram HTML renderer
# ---------------------------------------------------------------------------


def _escape_html(value: str) -> str:
    """Telegram HTML escape: ONLY & < >. Quote-escaping breaks parse_mode=HTML."""
    return html_module.escape(value, quote=False)


def render_html(node: Markup) -> str:  # noqa: PLR0911 — one return per AST variant
    """Render a Markup AST as Telegram-HTML."""
    if isinstance(node, Text):
        return _escape_html(node.value)
    if isinstance(node, Bold):
        return f"<b>{render_html(node.child)}</b>"
    if isinstance(node, Italic):
        return f"<i>{render_html(node.child)}</i>"
    if isinstance(node, Code):
        return f"<code>{render_html(node.child)}</code>"
    if isinstance(node, CodeBlock):
        return f"<pre>{render_html(node.child)}</pre>"
    if isinstance(node, RawHtml):
        # Trust the caller — they explicitly opted into raw HTML.
        return node.html
    if isinstance(node, Concat):
        return "".join(render_html(c) for c in node.children)
    raise TypeError(f"render_html: unknown Markup node {type(node).__name__}")


# ---------------------------------------------------------------------------
# WhatsApp renderer
# ---------------------------------------------------------------------------

# Strip raw-HTML tags down to their text content for graceful degradation.
_HTML_TAG_RE: Final[re.Pattern[str]] = re.compile(r"<[^>]+>")


def _strip_html(value: str) -> str:
    """Remove HTML tags, decode entities — graceful degradation for RawHtml."""
    return html_module.unescape(_HTML_TAG_RE.sub("", value))


def render_whatsapp(node: Markup) -> str:  # noqa: PLR0911 — one return per AST variant
    """Render a Markup AST as WhatsApp text (Twilio Body-compatible)."""
    if isinstance(node, Text):
        # WhatsApp is plain UTF-8 — no escaping required for the basic
        # styling chars. If a user-supplied value LITERALLY contains
        # `*` or `_`, WA will try to apply styling — that's a known
        # quirk, not worth fighting at the renderer.
        return node.value
    if isinstance(node, Bold):
        return f"*{render_whatsapp(node.child)}*"
    if isinstance(node, Italic):
        return f"_{render_whatsapp(node.child)}_"
    if isinstance(node, Code):
        return f"`{render_whatsapp(node.child)}`"
    if isinstance(node, CodeBlock):
        # WhatsApp code blocks are three backticks on their own line.
        return f"```\n{render_whatsapp(node.child)}\n```"
    if isinstance(node, RawHtml):
        return _strip_html(node.html)
    if isinstance(node, Concat):
        return "".join(render_whatsapp(c) for c in node.children)
    raise TypeError(f"render_whatsapp: unknown Markup node {type(node).__name__}")
