"""Telegram HTML message formatter — the only sanctioned outbound formatter.

Telegram supports a restricted HTML dialect documented at
https://core.telegram.org/bots/api#html-style. The five entities we use are:

    <b>...</b>      bold
    <i>...</i>      italic
    <code>...</code> inline monospace
    <pre>...</pre>   block monospace
    <a href="...">...</a>  hyperlink (https only)

Three characters MUST be HTML-escaped in any text segment, including inside
the supported tags: ``&``, ``<``, ``>``. Forgetting to escape user-supplied
input would let an attacker close a tag mid-message and inject markup; while
Telegram only renders the whitelisted tags, the resulting parse error makes
``sendMessage`` reject the entire request with a 400 — and the user sees
nothing.

We never accept Markdown anywhere. There is no fallback path that turns
backticks into ``<code>`` or asterisks into ``<b>``. The rule is enforced by
ESLint/ruff custom checks in CI.
"""

from __future__ import annotations

from html import escape as _html_escape
from urllib.parse import urlparse

__all__ = [
    "bold",
    "code",
    "italic",
    "link",
    "pre",
    "safe_text",
]


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------


def safe_text(value: object) -> str:
    """Escape ``& < >`` (and ``"`` for attribute safety) for Telegram HTML.

    ``None`` becomes an empty string so callers don't have to pre-check.
    Anything non-string is coerced via ``str()`` first — this is intentional
    so an int line number or a Decimal amount can flow straight in without
    every call site sprinkling ``str()``.
    """
    if value is None:
        return ""
    return _html_escape(str(value), quote=True)


# ---------------------------------------------------------------------------
# Inline entities
# ---------------------------------------------------------------------------


def bold(value: object) -> str:
    """``<b>...</b>`` with the inner text escaped."""
    return f"<b>{safe_text(value)}</b>"


def italic(value: object) -> str:
    """``<i>...</i>`` with the inner text escaped."""
    return f"<i>{safe_text(value)}</i>"


def code(value: object) -> str:
    """``<code>...</code>`` for inline monospace (GSTINs, amounts, file ids)."""
    return f"<code>{safe_text(value)}</code>"


def pre(value: object) -> str:
    """``<pre>...</pre>`` for multi-line monospace blocks."""
    return f"<pre>{safe_text(value)}</pre>"


# ---------------------------------------------------------------------------
# Links — strict allowlist
# ---------------------------------------------------------------------------


_ALLOWED_LINK_SCHEMES = frozenset({"https", "tg"})


def link(url: str, text: object) -> str:
    """``<a href="...">text</a>`` for trusted hyperlinks.

    Only ``https://`` and ``tg://`` URLs are allowed. ``http://`` and
    ``javascript:`` are rejected loudly — Telegram's renderer will accept
    ``http://`` but we refuse to ship insecure links from a CA workflow.

    Raises:
        ValueError: when ``url`` is empty, malformed, or uses a disallowed
            scheme.
    """
    if not isinstance(url, str) or not url:
        raise ValueError("link url must be a non-empty string")
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_LINK_SCHEMES:
        raise ValueError(
            f"link url scheme {parsed.scheme!r} not in allowlist "
            f"{sorted(_ALLOWED_LINK_SCHEMES)}"
        )
    # The href attribute itself needs escaping. ``safe_text`` already does
    # ``quote=True`` which escapes ``"`` to ``&quot;`` — exactly what we want.
    return f'<a href="{safe_text(url)}">{safe_text(text)}</a>'
