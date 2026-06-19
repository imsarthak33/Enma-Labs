"""Telegram implementation of :class:`MessageClient`.

Thin wrapper around the existing :mod:`app.services.telegram` module.
The wrapper exists so call sites don't import Telegram-specific
symbols — they use the factory and get a ``MessageClient``.

Behaviour is intentionally identical to the pre-W4 path: same retry
policy, same parse_mode=HTML, same error class (TelegramAPIError is
wrapped as :class:`app.services.messaging.MessagingError` so callers
can ``except MessagingError`` once and catch both channels' failures).
"""

from __future__ import annotations

from typing import Any

from app.services import telegram
from app.services.messaging.base import Markup, MessageClient, MessagingError
from app.services.messaging.render import render_html

__all__ = ["TelegramClient"]


class TelegramClient(MessageClient):
    """:class:`MessageClient` backed by the Telegram Bot API."""

    async def send_message(
        self,
        *,
        recipient: int | str,
        body: Markup,
        reply_to_id: int | str | None = None,
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        chat_id = _coerce_chat_id(recipient)
        reply_to = _coerce_message_id(reply_to_id)
        html_body = render_html(body)
        try:
            return await telegram.send_message(
                chat_id=chat_id,
                html_text=html_body,
                disable_notification=disable_notification,
                reply_to_message_id=reply_to,
            )
        except telegram.TelegramAPIError as exc:
            raise MessagingError(f"telegram send_message failed: {exc}") from exc

    async def send_document(
        self,
        *,
        recipient: int | str,
        file_bytes: bytes,
        filename: str,
        caption: Markup | None = None,
    ) -> dict[str, Any]:
        chat_id = _coerce_chat_id(recipient)
        caption_html = render_html(caption) if caption is not None else None
        try:
            return await telegram.send_document(
                chat_id=chat_id,
                file_bytes=file_bytes,
                filename=filename,
                caption_html=caption_html,
            )
        except telegram.TelegramAPIError as exc:
            raise MessagingError(f"telegram send_document failed: {exc}") from exc

    async def download_file(self, file_ref: str) -> bytes:
        try:
            return await telegram.download_file(file_ref)
        except telegram.TelegramAPIError as exc:
            raise MessagingError(f"telegram download_file failed: {exc}") from exc


def _coerce_chat_id(recipient: int | str) -> int:
    """Telegram expects a chat_id as int."""
    if isinstance(recipient, int):
        return recipient
    try:
        return int(recipient)
    except (ValueError, TypeError) as exc:
        raise MessagingError(
            f"TelegramClient: recipient must be an int chat_id, got {recipient!r}"
        ) from exc


def _coerce_message_id(value: int | str | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (ValueError, TypeError):
        # Telegram message ids are always ints; an unparseable value
        # means the caller threaded a WA-style ref by mistake. Silently
        # dropping is better than raising — the message still sends,
        # just not as a reply.
        return None
