"""Bank-statement email-ingest adapter (Track-A automation, Phase 2).

The first concrete provider on the env-gated pattern (see
:mod:`app.services.providers.base`). Banks email statements as attachments;
this adapter polls a monitored IMAP mailbox, pulls the bank-statement
attachments (PDF / CSV / Excel), and hands their bytes to the caller, which
feeds them through the same sniff → route → Brain ingestion path an upload
takes. The "I didn't upload it and it reconciled" moment — once a mailbox is
configured.

Dormant by default: with no IMAP credentials in the env,
:func:`get_email_ingest_client` returns :class:`NullEmailIngestClient`, whose
fetch returns nothing and never touches the network. Paste the mailbox
credentials into the env and the IMAP client activates — no code change.

IMAP I/O is blocking (:mod:`imaplib`), so the real client runs it in a worker
thread via :func:`asyncio.to_thread` to stay friendly to the event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import email
import imaplib
from dataclasses import dataclass
from email.message import Message
from typing import Final, Protocol, runtime_checkable

from app.config import Settings
from app.logging_setup import get_logger

__all__ = [
    "BANK_ATTACHMENT_EXTENSIONS",
    "EmailIngestProvider",
    "FetchedAttachment",
    "ImapEmailIngestClient",
    "NullEmailIngestClient",
    "get_email_ingest_client",
]

_log = get_logger(__name__)

# Attachment types our bank ingestion can read (PDF e-statement / CSV export /
# Excel). Anything else in the mailbox is ignored.
BANK_ATTACHMENT_EXTENSIONS: Final[tuple[str, ...]] = (".pdf", ".csv", ".xls", ".xlsx")

# Don't pull unbounded mail in a single poll — a runaway mailbox shouldn't
# blast the ingestion path. Tunable; conservative by default.
_MAX_MESSAGES_PER_POLL: Final[int] = 50


@dataclass(frozen=True)
class FetchedAttachment:
    """One bank-statement attachment pulled from the mailbox."""

    filename: str
    content: bytes
    sender: str
    subject: str


@runtime_checkable
class EmailIngestProvider(Protocol):
    """Polls a mailbox for bank-statement attachments."""

    @property
    def is_configured(self) -> bool: ...

    async def fetch_statements(self) -> list[FetchedAttachment]:
        """Return new bank-statement attachments, marking their mail seen."""
        ...


class NullEmailIngestClient:
    """No-op adapter used when no mailbox is configured (the default)."""

    @property
    def is_configured(self) -> bool:
        return False

    async def fetch_statements(self) -> list[FetchedAttachment]:
        return []


class ImapEmailIngestClient:
    """Real IMAP adapter — active only when full credentials are present."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        mailbox: str,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._mailbox = mailbox

    @property
    def is_configured(self) -> bool:
        return True

    async def fetch_statements(self) -> list[FetchedAttachment]:
        """Pull unseen bank-statement attachments (IMAP I/O off the loop)."""
        return await asyncio.to_thread(self._fetch_sync)

    def _fetch_sync(self) -> list[FetchedAttachment]:
        out: list[FetchedAttachment] = []
        conn = imaplib.IMAP4_SSL(self._host, self._port)
        try:
            conn.login(self._username, self._password)
            conn.select(self._mailbox)
            typ, data = conn.search(None, "UNSEEN")
            if typ != "OK" or not data or not data[0]:
                return out
            message_ids = [m.decode() for m in data[0].split()[:_MAX_MESSAGES_PER_POLL]]
            for num in message_ids:
                fetched = self._fetch_one(conn, num)
                out.extend(fetched)
                # Mark seen only after a successful read so a crash mid-poll
                # leaves the mail to be retried next run.
                conn.store(num, "+FLAGS", "\\Seen")
        finally:
            # Logout failures must not mask a good fetch.
            with contextlib.suppress(Exception):
                conn.logout()
        return out

    @staticmethod
    def _fetch_one(conn: imaplib.IMAP4_SSL, num: str) -> list[FetchedAttachment]:
        typ, msg_data = conn.fetch(num, "(RFC822)")
        if typ != "OK" or not msg_data:
            return []
        raw = next((part[1] for part in msg_data if isinstance(part, tuple)), None)
        if not isinstance(raw, bytes | bytearray):
            return []
        msg = email.message_from_bytes(bytes(raw))
        sender = str(msg.get("From", ""))
        subject = str(msg.get("Subject", ""))
        return _attachments_from(msg, sender=sender, subject=subject)


def _attachments_from(
    msg: Message, *, sender: str, subject: str
) -> list[FetchedAttachment]:
    """Extract bank-statement attachments from a parsed email message."""
    out: list[FetchedAttachment] = []
    for part in msg.walk():
        filename = part.get_filename()
        if not filename or not filename.lower().endswith(BANK_ATTACHMENT_EXTENSIONS):
            continue
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes) and payload:
            out.append(
                FetchedAttachment(
                    filename=filename,
                    content=payload,
                    sender=sender,
                    subject=subject,
                )
            )
    return out


def get_email_ingest_client(settings: Settings) -> EmailIngestProvider:
    """Return the real IMAP client when configured, else the no-op client.

    The single activation point: with no mailbox credentials in the env this
    returns :class:`NullEmailIngestClient` and the feature stays dormant.
    """
    if not settings.is_email_ingest_configured():
        return NullEmailIngestClient()
    assert settings.email_ingest_host is not None  # narrowed by is_*_configured
    assert settings.email_ingest_username is not None
    assert settings.email_ingest_password is not None
    _log.info("email_ingest_client_active", host=settings.email_ingest_host)
    return ImapEmailIngestClient(
        host=settings.email_ingest_host,
        port=settings.email_ingest_port,
        username=settings.email_ingest_username,
        password=settings.email_ingest_password.get_secret_value(),
        mailbox=settings.email_ingest_mailbox,
    )
