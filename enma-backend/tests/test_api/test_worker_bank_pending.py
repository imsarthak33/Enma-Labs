"""Tests — bank-statement uploads hold a pending assignment when un-routed.

The fix for the live-smoke gap: a bank statement that can't be routed on
upload (multiple clients, no caption) parks its parsed transactions on a
pending-assignment row, so a free-form name reply resumes ingestion instead
of dropping the file. These tests cover the hold + the resume dispatch at
the function seam (DB / telegram mocked).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.routes import worker as wk
from app.services.bank_import import BankTxn, ParsedBankStatement


def _statement() -> ParsedBankStatement:
    return ParsedBankStatement(
        transactions=(
            BankTxn(
                txn_date=date(2026, 1, 2),
                narration="TO TRANSFER GOLDEN THREAD",
                amount=Decimal("51000.00"),
                direction="debit",
            ),
        )
    )


def _firm() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4())


async def test_unrouted_bank_statement_is_held_pending() -> None:
    active = [
        SimpleNamespace(id=uuid.uuid4(), trade_name="zeroX Energies"),
        SimpleNamespace(id=uuid.uuid4(), trade_name="S.S Traders"),
    ]
    pending_row = SimpleNamespace(id=uuid.uuid4())
    pending_q = MagicMock()
    pending_q.create = AsyncMock(return_value=pending_row)
    pending_q.attach_prompt_message = AsyncMock()

    with (
        patch.object(wk, "PendingAssignmentQuery", return_value=pending_q),
        patch.object(
            wk.telegram, "send_message", AsyncMock(return_value={"message_id": 555})
        ),
    ):
        await wk._hold_bank_statement_pending(
            session=AsyncMock(),
            firm=_firm(),
            chat_id=42,
            reply_to_message_id=7,
            statement=_statement(),
            file_id="tg-file-9",
            active=active,
        )

    pending_q.create.assert_awaited_once()
    kwargs = pending_q.create.await_args.kwargs
    # Tagged with the bank kind sentinel so resume runs bank ingestion.
    assert kwargs["extraction_document_type"] == wk._PENDING_KIND_BANK
    # Parsed transactions parked on the row (no re-download needed on resume).
    assert kwargs["extraction"]["transactions"][0]["amount"] == "51000.00"
    assert kwargs["file_ids"] == ["tg-file-9"]
    # Prompt message id recorded against the pending row.
    pending_q.attach_prompt_message.assert_awaited_once_with(pending_row.id, 555)


async def test_resume_dispatches_bank_ingestion() -> None:
    client = SimpleNamespace(id=uuid.uuid4(), trade_name="S.S Traders")
    row = SimpleNamespace(
        id=uuid.uuid4(),
        resolved_client_id=client.id,
        message_id=7,
        file_ids=["tg-file-9"],
        extraction={"transactions": [
            {
                "date": "2026-01-02",
                "narration": "TO TRANSFER GOLDEN THREAD",
                "amount": "51000.00",
                "direction": "debit",
            }
        ]},
        extraction_document_type=wk._PENDING_KIND_BANK,
    )

    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = row
    session = AsyncMock()
    session.execute = AsyncMock(return_value=exec_result)

    clients_q = MagicMock()
    clients_q.get_by_id = AsyncMock(return_value=client)

    with (
        patch.object(wk, "PendingAssignmentQuery", return_value=MagicMock()),
        patch.object(wk, "ClientQuery", return_value=clients_q),
        patch.object(wk, "_bank_ingest_for_client", AsyncMock()) as ingest,
    ):
        await wk._resume_pipeline_for_pending_assignment(
            session=session,
            firm=_firm(),
            chat_id=42,
            pending_assignment_id=row.id,
        )

    ingest.assert_awaited_once()
    kwargs = ingest.await_args.kwargs
    assert kwargs["client"] is client
    assert kwargs["statement"].transactions[0].amount == Decimal("51000.00")
    assert kwargs["reply_to_message_id"] == 7
