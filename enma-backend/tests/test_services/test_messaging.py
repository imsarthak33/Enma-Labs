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


def _firm(
    *,
    channel: str = "telegram",
    wa_provider: str | None = None,
    wa_account_id: str | None = None,
    wa_phone: str | None = None,
    wa_token_encrypted: bytes | None = None,
) -> MagicMock:
    f = MagicMock()
    f.id = uuid.uuid4()
    f.primary_channel = channel
    f.whatsapp_provider = wa_provider
    f.whatsapp_account_id = wa_account_id
    f.whatsapp_phone_number = wa_phone
    f.whatsapp_auth_token_encrypted = wa_token_encrypted
    return f


def _user(*, channel: str | None) -> MagicMock:
    u = MagicMock()
    u.primary_channel = channel
    return u


@pytest.fixture(autouse=True)
def _reset_wa_cache():
    factory.reset_whatsapp_cache()
    yield
    factory.reset_whatsapp_cache()


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

    def test_client_for_whatsapp_returns_whatsapp_client(self) -> None:
        from app.services.messaging.whatsapp_client import WhatsAppClient
        from app.utils.crypto import encrypt_token

        firm = _firm(
            channel="whatsapp",
            wa_provider="twilio",
            wa_account_id="ACxxxx",
            wa_phone="+14155238886",
            wa_token_encrypted=encrypt_token("test_auth_token"),
        )
        client = factory.client_for(firm=firm)
        assert isinstance(client, WhatsAppClient)

    def test_client_for_whatsapp_cached_per_firm(self) -> None:
        from app.utils.crypto import encrypt_token

        firm = _firm(
            channel="whatsapp",
            wa_provider="twilio",
            wa_account_id="ACxxxx",
            wa_phone="+14155238886",
            wa_token_encrypted=encrypt_token("test_auth_token"),
        )
        first = factory.client_for(firm=firm)
        second = factory.client_for(firm=firm)
        assert first is second  # cached, same instance

    def test_client_for_whatsapp_unsupported_provider_raises(self) -> None:
        firm = _firm(
            channel="whatsapp",
            wa_provider="meta_cloud",  # not yet wired
            wa_account_id="x",
            wa_phone="+1",
            wa_token_encrypted=b"x",
        )
        with pytest.raises(MessagingError, match="unsupported provider"):
            factory.client_for(firm=firm)

    def test_client_for_whatsapp_missing_token_raises(self) -> None:
        firm = _firm(
            channel="whatsapp",
            wa_provider="twilio",
            wa_account_id="ACxxxx",
            wa_phone="+14155238886",
            wa_token_encrypted=None,
        )
        with pytest.raises(MessagingError, match="no encrypted auth_token"):
            factory.client_for(firm=firm)

    def test_client_for_whatsapp_corrupt_token_raises(self) -> None:
        firm = _firm(
            channel="whatsapp",
            wa_provider="twilio",
            wa_account_id="ACxxxx",
            wa_phone="+14155238886",
            wa_token_encrypted=b"not_a_real_fernet_blob",
        )
        with pytest.raises(MessagingError, match="failed to decrypt auth_token"):
            factory.client_for(firm=firm)


# ---------------------------------------------------------------------------
# WhatsAppClient — via httpx.MockTransport
# ---------------------------------------------------------------------------


import httpx  # noqa: E402 — co-located with the WA tests
from app.services.messaging.whatsapp_client import WhatsAppClient  # noqa: E402


def _wa_client_with_mock(handler) -> WhatsAppClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return WhatsAppClient(
        account_sid="ACtestsid",
        auth_token="testtoken",
        from_number="+14155238886",
        http_client=http,
    )


class TestWhatsAppClient:
    @pytest.mark.asyncio
    async def test_send_message_posts_to_twilio_with_expected_form(self) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("authorization", "")
            captured["body"] = request.content.decode()
            return httpx.Response(201, json={"sid": "SMxxxx", "status": "queued"})

        client = _wa_client_with_mock(handler)
        result = await client.send_message(
            recipient="+919876543210",
            body=Concat((Text("hi "), Bold(Text("CA")))),
        )
        assert result["sid"] == "SMxxxx"
        assert "ACtestsid/Messages.json" in captured["url"]
        assert captured["auth"].startswith("Basic ")
        # form-encoded From/To/Body — both must carry whatsapp: prefix.
        assert "From=whatsapp%3A%2B14155238886" in captured["body"]
        assert "To=whatsapp%3A%2B919876543210" in captured["body"]
        assert "hi+%2ACA%2A" in captured["body"]  # "hi *CA*"

    @pytest.mark.asyncio
    async def test_send_message_4xx_raises(self) -> None:
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"code": 21211, "message": "Invalid 'To'"})

        client = _wa_client_with_mock(handler)
        with pytest.raises(MessagingError, match="twilio send_message HTTP 400"):
            await client.send_message(recipient="+1", body=Text("x"))

    @pytest.mark.asyncio
    async def test_send_message_rejects_int_recipient(self) -> None:
        client = _wa_client_with_mock(lambda r: httpx.Response(201, json={}))
        with pytest.raises(MessagingError, match="must be an E.164 phone string"):
            await client.send_message(recipient=12345, body=Text("x"))

    @pytest.mark.asyncio
    async def test_send_message_empty_body_raises(self) -> None:
        client = _wa_client_with_mock(lambda r: httpx.Response(201, json={}))
        with pytest.raises(MessagingError, match="empty body"):
            await client.send_message(recipient="+1", body=Text(""))

    @pytest.mark.asyncio
    async def test_send_document_not_implemented(self) -> None:
        client = _wa_client_with_mock(lambda r: httpx.Response(201, json={}))
        with pytest.raises(NotImplementedError, match="P2.b.2"):
            await client.send_document(
                recipient="+1", file_bytes=b"x", filename="x.csv"
            )

    @pytest.mark.asyncio
    async def test_download_file_uses_basic_auth(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("authorization", "")
            return httpx.Response(200, content=b"PDF_BYTES")

        client = _wa_client_with_mock(handler)
        out = await client.download_file(
            "https://api.twilio.com/2010-04-01/Accounts/AC/Messages/SM/Media/ME"
        )
        assert out == b"PDF_BYTES"
        assert captured["auth"].startswith("Basic ")

    @pytest.mark.asyncio
    async def test_download_file_rejects_non_https(self) -> None:
        client = _wa_client_with_mock(lambda r: httpx.Response(200))
        with pytest.raises(MessagingError, match="expected an https Twilio Media URL"):
            await client.download_file("file:///etc/passwd")
