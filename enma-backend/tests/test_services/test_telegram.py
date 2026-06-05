"""Tests for the Telegram client.

We never hit the real API. ``httpx.MockTransport`` intercepts requests and
lets us assert on the URL, method, body, and headers.
"""

from __future__ import annotations

import json

import httpx
import pytest
from app.services import telegram


def _ok_response(method_url_part: str, result: object) -> httpx.Response:
    return httpx.Response(
        httpx.codes.OK,
        json={"ok": True, "result": result},
        request=httpx.Request("POST", method_url_part),
    )


def _make_client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


@pytest.mark.asyncio
async def test_send_message_uses_html_parse_mode() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok_response(str(request.url), {"message_id": 7})

    client = _make_client(httpx.MockTransport(handler))
    try:
        result = await telegram.send_message(
            chat_id=42, html_text="<b>hello</b>", client=client
        )
    finally:
        await client.aclose()

    assert result == {"message_id": 7}
    assert len(seen) == 1
    req = seen[0]
    assert req.method == "POST"
    assert "/sendMessage" in str(req.url)
    body = json.loads(req.content.decode())
    assert body["chat_id"] == 42
    assert body["text"] == "<b>hello</b>"
    assert body["parse_mode"] == "HTML"
    # Web-page previews are off by default so links don't expand.
    assert body["disable_web_page_preview"] is True


@pytest.mark.asyncio
async def test_send_message_optional_reply_to() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok_response(str(request.url), {"message_id": 1})

    client = _make_client(httpx.MockTransport(handler))
    try:
        await telegram.send_message(
            chat_id=1, html_text="x", reply_to_message_id=99, client=client
        )
    finally:
        await client.aclose()
    body = json.loads(seen[0].content.decode())
    assert body["reply_to_message_id"] == 99


@pytest.mark.asyncio
async def test_send_message_raises_on_non_2xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom", request=request)

    client = _make_client(httpx.MockTransport(handler))
    try:
        with pytest.raises(telegram.TelegramAPIError) as exc_info:
            await telegram.send_message(chat_id=1, html_text="x", client=client)
    finally:
        await client.aclose()
    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_send_message_raises_on_ok_false() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            httpx.codes.OK,
            json={"ok": False, "description": "chat not found"},
            request=request,
        )

    client = _make_client(httpx.MockTransport(handler))
    try:
        with pytest.raises(telegram.TelegramAPIError):
            await telegram.send_message(chat_id=1, html_text="x", client=client)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_download_file_two_step_flow() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if "/getFile" in url:
            return _ok_response(url, {"file_path": "photos/abc.jpg"})
        # Final file download.
        return httpx.Response(httpx.codes.OK, content=b"\x89PNG\r\n", request=request)

    client = _make_client(httpx.MockTransport(handler))
    try:
        data = await telegram.download_file("file-123", client=client)
    finally:
        await client.aclose()
    assert data.startswith(b"\x89PNG")
    assert any("/getFile" in c for c in calls)
    assert any("photos/abc.jpg" in c for c in calls)


@pytest.mark.asyncio
async def test_send_document_multipart() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok_response(str(request.url), {"message_id": 11})

    client = _make_client(httpx.MockTransport(handler))
    try:
        result = await telegram.send_document(
            chat_id=5,
            file_bytes=b"PDF-DATA",
            filename="report.pdf",
            caption_html="<b>Q1 report</b>",
            client=client,
        )
    finally:
        await client.aclose()
    assert result == {"message_id": 11}
    req = seen[0]
    assert "/sendDocument" in str(req.url)
    # multipart bodies include both the file and the caption form-fields.
    body = req.content
    assert b"report.pdf" in body
    assert b"<b>Q1 report</b>" in body
    assert b"HTML" in body  # parse_mode form field


@pytest.mark.asyncio
async def test_get_client_singleton_and_close() -> None:
    a = telegram.get_client()
    b = telegram.get_client()
    assert a is b
    await telegram.close_client()
    # After close a fresh client is built.
    c = telegram.get_client()
    assert c is not a
    await telegram.close_client()
