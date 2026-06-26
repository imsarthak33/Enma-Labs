"""Tests — acquisition-agnostic auto-ingest seam (Phase 7a)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import auto_ingest

# ICICI-style bank CSV (Debit/Credit columns) — parses to one debit txn.
_BANK_CSV = (
    b"Txn Date,Particulars,Debit,Credit,Balance\n"
    b"15-08-2025,RTGS ACME SUPPLIES,11800.00,0.00,40000.00\n"
)


def _firm(admin_chat_id: int | None = 42) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), admin_chat_id=admin_chat_id)


def _client() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), trade_name="S.S Traders")


async def _run(*, file_bytes: bytes, firm: SimpleNamespace) -> auto_ingest.AutoIngestResult:
    brain = MagicMock()
    brain.record_dedup_bulk = AsyncMock(return_value=(1, 0))
    deliver = AsyncMock(return_value=SimpleNamespace(summary="reconciled X"))
    with (
        patch.object(auto_ingest, "BrainEventQuery", return_value=brain),
        patch.object(
            auto_ingest.bank_recon_runner, "bank_reconcile_and_deliver", deliver
        ),
    ):
        result = await auto_ingest.ingest_bank_statement_bytes(
            session=AsyncMock(),
            firm=firm,
            client=_client(),
            file_bytes=file_bytes,
            filename="statement.csv",
        )
    result._deliver = deliver  # type: ignore[attr-defined]  # surface for assert
    return result


async def test_ingests_bank_csv_for_known_client() -> None:
    res = await _run(file_bytes=_BANK_CSV, firm=_firm())
    assert res.ingested is True
    assert res.summary == "reconciled X"
    res._deliver.assert_awaited_once()  # type: ignore[attr-defined]


async def test_non_bank_bytes_not_ingested() -> None:
    res = await _run(file_bytes=b"hello, this is not a statement", firm=_firm())
    assert res.ingested is False
    res._deliver.assert_not_awaited()  # type: ignore[attr-defined]


async def test_no_admin_chat_skips() -> None:
    res = await _run(file_bytes=_BANK_CSV, firm=_firm(admin_chat_id=None))
    assert res.ingested is False
    res._deliver.assert_not_awaited()  # type: ignore[attr-defined]
