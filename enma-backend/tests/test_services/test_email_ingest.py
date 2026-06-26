"""Tests — bank-statement email-ingest adapter (Track-A automation P2).

Covers the env-gated factory (dormant unless credentials present) and the
attachment extraction. Live IMAP I/O is not exercised (needs a server); the
pure parsing + the activation gate are what carry the risk.
"""

from __future__ import annotations

import uuid
from email.message import EmailMessage

from app.config import Settings
from app.services.providers.email_ingest import (
    FetchedAttachment,
    ImapEmailIngestClient,
    NullEmailIngestClient,
    _attachments_from,
    client_id_from_recipients,
    get_email_ingest_client,
    ingest_address_for_client,
)


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def test_unconfigured_returns_null_client() -> None:
    client = get_email_ingest_client(_settings())
    assert isinstance(client, NullEmailIngestClient)
    assert client.is_configured is False


async def test_null_client_fetches_nothing() -> None:
    assert await NullEmailIngestClient().fetch_statements() == []


def test_configured_returns_imap_client() -> None:
    settings = _settings(
        email_ingest_host="imap.gmail.com",
        email_ingest_username="bankbot@firm.com",
        email_ingest_password="app-password",
    )
    assert settings.is_email_ingest_configured() is True
    client = get_email_ingest_client(settings)
    assert isinstance(client, ImapEmailIngestClient)
    assert client.is_configured is True


def test_partial_credentials_stay_dormant() -> None:
    # Host present but no password → not configured → Null (dormant).
    settings = _settings(email_ingest_host="imap.gmail.com")
    assert settings.is_email_ingest_configured() is False
    assert isinstance(get_email_ingest_client(settings), NullEmailIngestClient)


def _email_with(*attachments: tuple[str, str, str, bytes]) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "statements@bank.example"
    msg["Subject"] = "Your account statement"
    msg.set_content("Please find your statement attached.")
    for maintype, subtype, filename, data in attachments:
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return msg


def test_attachments_filtered_to_bank_types() -> None:
    msg = _email_with(
        ("application", "pdf", "statement.pdf", b"%PDF-1.4 fake"),
        ("text", "csv", "ledger.csv", b"Date,Amount\n01/01/2026,100"),
        ("image", "png", "logo.png", b"\x89PNG fake"),  # ignored
    )
    out = _attachments_from(msg)
    names = sorted(a.filename for a in out)
    assert names == ["ledger.csv", "statement.pdf"]  # png dropped
    pdf = next(a for a in out if a.filename == "statement.pdf")
    assert pdf.content == b"%PDF-1.4 fake"
    assert pdf.sender == "statements@bank.example"


def test_no_attachments_yields_empty() -> None:
    msg = EmailMessage()
    msg["From"] = "x@y.com"
    msg.set_content("just text, no attachment")
    assert _attachments_from(msg) == []


def test_fetched_attachment_is_frozen() -> None:
    a = FetchedAttachment(filename="s.pdf", content=b"x", sender="a", subject="b")
    assert a.filename == "s.pdf"
    assert a.recipients == ()


# ── per-client deterministic routing ──────────────────────────────────────


def test_ingest_address_round_trips_to_client_id() -> None:
    cid = uuid.uuid4()
    addr = ingest_address_for_client(cid, domain="ingest.enmalabs.in")
    assert addr == f"client-{cid}@ingest.enmalabs.in"
    assert client_id_from_recipients((addr,)) == cid


def test_routing_reads_client_id_from_email_recipient() -> None:
    cid = uuid.uuid4()
    msg = _email_with(("application", "pdf", "s.pdf", b"%PDF fake"))
    msg["Delivered-To"] = f"client-{cid}@ingest.enmalabs.in"
    out = _attachments_from(msg)
    assert out[0].client_id == cid


def test_routing_none_when_no_ingest_address() -> None:
    assert client_id_from_recipients(("someone@gmail.com", "list@x.org")) is None
    msg = _email_with(("text", "csv", "s.csv", b"a,b"))
    msg["To"] = "statements@bank.example"
    assert _attachments_from(msg)[0].client_id is None
