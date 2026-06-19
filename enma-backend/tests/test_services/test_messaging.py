"""Tests for the W4-P2 messaging abstraction.

Three layers covered:

  * Markup AST + renderers (pure functions, exhaustive)
  * TelegramClient wraps app.services.telegram correctly
  * factory.client_for / resolve_channel pick the right concrete
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from app.services.messaging import factory
from app.services.messaging.base import (
    Bold,
    Code,
    CodeBlock,
    Concat,
    Italic,
    MessagingError,
    RawHtml,
    Text,
)
from app.services.messaging.render import render_html, render_whatsapp
from app.services.messaging.telegram_client import TelegramClient

# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


class TestRenderHtml:
    def test_plain_text_escaped(self) -> None:
        assert render_html(Text("a < b & c")) == "a &lt; b &amp; c"

    def test_quotes_not_escaped(self) -> None:
        # Telegram parse_mode=HTML BREAKS on &quot;/&#x27; — keep them literal.
        assert render_html(Text("it's \"yes\"")) == "it's \"yes\""

    def test_bold_wraps_b(self) -> None:
        assert render_html(Bold(Text("x"))) == "<b>x</b>"

    def test_italic_wraps_i(self) -> None:
        assert render_html(Italic(Text("x"))) == "<i>x</i>"

    def test_code_wraps_code(self) -> None:
        assert render_html(Code(Text("4b8d91e0"))) == "<code>4b8d91e0</code>"

    def test_code_block_wraps_pre(self) -> None:
        assert render_html(CodeBlock(Text("hash"))) == "<pre>hash</pre>"

    def test_concat_joins(self) -> None:
        node = Concat(
            (Text("Verdict: "), Bold(Text("CLEAN")), Text(" — ready."))
        )
        assert render_html(node) == "Verdict: <b>CLEAN</b> — ready."

    def test_raw_html_passes_through(self) -> None:
        assert render_html(RawHtml("<u>x</u>")) == "<u>x</u>"

    def test_nested_bold_italic(self) -> None:
        node = Bold(Italic(Text("emphasis")))
        assert render_html(node) == "<b><i>emphasis</i></b>"


class TestRenderWhatsapp:
    def test_plain_text_not_escaped(self) -> None:
        # WA wire is plain UTF-8 — no HTML entity expansion.
        assert render_whatsapp(Text("a < b & c")) == "a < b & c"

    def test_bold_wraps_asterisks(self) -> None:
        assert render_whatsapp(Bold(Text("x"))) == "*x*"

    def test_italic_wraps_underscores(self) -> None:
        assert render_whatsapp(Italic(Text("x"))) == "_x_"

    def test_code_wraps_backticks(self) -> None:
        assert render_whatsapp(Code(Text("4b8d91e0"))) == "`4b8d91e0`"

    def test_code_block_uses_triple_backticks(self) -> None:
        rendered = render_whatsapp(CodeBlock(Text("hash")))
        assert rendered.startswith("```\n")
        assert rendered.endswith("\n```")

    def test_concat_joins(self) -> None:
        node = Concat(
            (Text("Verdict: "), Bold(Text("CLEAN")), Text(" — ready."))
        )
        assert render_whatsapp(node) == "Verdict: *CLEAN* — ready."

    def test_raw_html_degraded_to_text(self) -> None:
        # WA strips tags + unescapes entities so RawHtml degrades cleanly.
        assert render_whatsapp(RawHtml("<u>x &amp; y</u>")) == "x & y"


# ---------------------------------------------------------------------------
# TelegramClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_telegram_client_renders_html_and_calls_underlying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_send(*, chat_id: int, html_text: str, **kwargs: Any) -> dict[str, Any]:
        captured["chat_id"] = chat_id
        captured["html_text"] = html_text
        captured["kwargs"] = kwargs
        return {"message_id": 7}

    monkeypatch.setattr("app.services.telegram.send_message", fake_send)
    client = TelegramClient()
    body = Concat((Text("hi "), Bold(Text("CA"))))
    result = await client.send_message(recipient=12345, body=body)
    assert result == {"message_id": 7}
    assert captured["chat_id"] == 12345
    assert captured["html_text"] == "hi <b>CA</b>"


@pytest.mark.asyncio
async def test_telegram_client_coerces_string_chat_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The interface allows str recipients; TG client must coerce to int."""
    captured: dict[str, Any] = {}

    async def fake_send(*, chat_id: int, **_kw: Any) -> dict[str, Any]:
        captured["chat_id"] = chat_id
        return {}

    monkeypatch.setattr("app.services.telegram.send_message", fake_send)
    await TelegramClient().send_message(recipient="12345", body=Text("hi"))
    assert captured["chat_id"] == 12345
    assert isinstance(captured["chat_id"], int)


@pytest.mark.asyncio
async def test_telegram_client_wraps_underlying_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.telegram import TelegramAPIError

    async def fake_send(**_kw: Any) -> dict[str, Any]:
        raise TelegramAPIError("sendMessage", 500, "oops")

    monkeypatch.setattr("app.services.telegram.send_message", fake_send)
    with pytest.raises(MessagingError, match="telegram send_message failed"):
        await TelegramClient().send_message(recipient=1, body=Text("x"))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _firm(*, channel: str = "telegram") -> MagicMock:
    f = MagicMock()
    f.id = uuid.uuid4()
    f.primary_channel = channel
    return f


def _user(*, channel: str | None) -> MagicMock:
    u = MagicMock()
    u.primary_channel = channel
    return u


class TestFactory:
    def test_default_firm_is_telegram(self) -> None:
        ch = factory.resolve_channel(firm=_firm())
        assert ch == "telegram"

    def test_user_override_to_whatsapp(self) -> None:
        ch = factory.resolve_channel(firm=_firm(channel="telegram"), user=_user(channel="whatsapp"))
        assert ch == "whatsapp"

    def test_user_none_inherits_firm(self) -> None:
        ch = factory.resolve_channel(firm=_firm(channel="whatsapp"), user=_user(channel=None))
        assert ch == "whatsapp"

    def test_unknown_channel_falls_back_to_telegram(self) -> None:
        ch = factory.resolve_channel(firm=_firm(channel="instagram_dm"))
        assert ch == "telegram"

    def test_client_for_telegram_returns_telegram_client(self) -> None:
        client = factory.client_for(firm=_firm(channel="telegram"))
        assert isinstance(client, TelegramClient)

    def test_client_for_whatsapp_raises_until_wired(self) -> None:
        """P2.a stub — WA wiring lands in P2.b."""
        with pytest.raises(MessagingError, match="WhatsApp client not yet wired"):
            factory.client_for(firm=_firm(channel="whatsapp"))
