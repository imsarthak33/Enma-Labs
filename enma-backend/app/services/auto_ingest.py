"""Acquisition-agnostic ingestion seam (Track-A automation, Phase 7).

The byte-ingestion entry point an *automated* source (email poll today; GSP
pull later) calls once it has raw document bytes and a **known** client — no
Telegram download, no "which client?" prompt (the source already resolved
the client). It sniffs the bytes, parses, lands them in ``brain_events``, and
runs the matching reconciliation, delivering the result to the firm admin.

Today it handles **bank statements** (the email use case): CSV exports and
PDF e-statements, reusing the same parsers + 180-day recon the upload path
uses. Extending to Tally / GSTR-2B is a matter of adding their sniffs here.

Deterministic; no LLM. Re-ingesting the same statement is a no-op at the
``brain_events`` content-hash dedup layer.
"""

from __future__ import annotations

from typing import Any

from app.db.queries.brain_events import BrainEventQuery
from app.logging_setup import get_logger
from app.services import bank_import, bank_pdf_import, bank_recon_runner

__all__ = ["AutoIngestResult", "ingest_bank_statement_bytes"]

_log = get_logger(__name__)


class AutoIngestResult:
    """Outcome of an auto-ingest attempt."""

    def __init__(self, *, ingested: bool, summary: str | None = None) -> None:
        self.ingested = ingested
        self.summary = summary


def _parse_bank_bytes(file_bytes: bytes) -> bank_import.ParsedBankStatement | None:
    """Sniff + parse bank-statement bytes (CSV or PDF), else ``None``."""
    if bank_import.looks_like_bank_csv(file_bytes):
        try:
            return bank_import.parse_bank_statement(file_bytes)
        except bank_import.BankStatementParseError:
            return None
    if bank_pdf_import.looks_like_bank_pdf(file_bytes):
        try:
            return bank_pdf_import.parse_bank_pdf(file_bytes)
        except bank_import.BankStatementParseError:
            return None
    return None


async def ingest_bank_statement_bytes(
    *,
    session: Any,
    firm: Any,
    client: Any,
    file_bytes: bytes,
    filename: str,
) -> AutoIngestResult:
    """Auto-ingest a bank statement (CSV/PDF) for an already-resolved client.

    Lands the transactions in ``brain_events(source='bank')`` and runs the
    180-day recon, delivering the summary/CSV to the firm admin. Returns
    ``ingested=False`` when the bytes aren't a parseable bank statement, or
    when the firm has no linked Telegram chat to deliver to.
    """
    if firm.admin_chat_id is None:
        _log.info("auto_ingest_skipped_no_admin_chat", client_id=str(client.id))
        return AutoIngestResult(ingested=False)

    statement = _parse_bank_bytes(file_bytes)
    if statement is None:
        _log.info("auto_ingest_not_a_bank_statement", filename=filename)
        return AutoIngestResult(ingested=False)

    rows = bank_recon_runner.txns_to_brain_rows(
        transactions=list(statement.transactions), client_id=client.id
    )
    brain_q = BrainEventQuery(session=session, ca_firm_id=firm.id)
    inserted, skipped = await brain_q.record_dedup_bulk(rows=rows)
    await session.commit()
    _log.info(
        "auto_ingest_bank_landed",
        ca_firm_id=str(firm.id),
        client_id=str(client.id),
        filename=filename,
        transactions=len(statement.transactions),
        inserted=inserted,
        skipped=skipped,
    )

    delivery = await bank_recon_runner.bank_reconcile_and_deliver(
        session=session,
        firm=firm,
        client=client,
        transactions=list(statement.transactions),
        chat_id=firm.admin_chat_id,
    )
    return AutoIngestResult(ingested=True, summary=delivery.summary)
