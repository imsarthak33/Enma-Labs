"""Tests — Tally exports hold a pending assignment when un-routed.

Same fix as the bank leg: a Tally export that can't be routed on upload
(its company name doesn't match a client, no caption) parks its parsed
voucher rows on a pending-assignment row, so a free-form name reply resumes
ingestion instead of dropping the file. DB / telegram mocked at the seam.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.routes import worker as wk


def _base_rows() -> list[dict]:
    return [
        {
            "source": "tally",
            "event_type": "voucher_purchase",
            "dedup_key": "hash-1",
            "occurred_at": datetime(2026, 1, 2, tzinfo=UTC),
            "payload": {"voucher_number": "1", "party_ledger": "ACME"},
        }
    ]


def _firm() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4())


def test_tally_rows_json_round_trip() -> None:
    base = _base_rows()
    serialised = wk._tally_rows_to_json(base)
    assert serialised[0]["occurred_at"] == "2026-01-02T00:00:00+00:00"
    assert "source" not in serialised[0]  # added back on rebuild, not stored
    rebuilt = wk._tally_rows_from_json(serialised)
    assert rebuilt[0]["source"] == "tally"
    assert rebuilt[0]["occurred_at"] == datetime(2026, 1, 2, tzinfo=UTC)
    assert rebuilt[0]["dedup_key"] == "hash-1"
    assert rebuilt[0]["payload"]["party_ledger"] == "ACME"


async def test_unrouted_tally_export_is_held_pending() -> None:
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
            wk.telegram, "send_message", AsyncMock(return_value={"message_id": 77})
        ),
    ):
        await wk._hold_tally_export_pending(
            session=AsyncMock(),
            firm=_firm(),
            chat_id=42,
            reply_to_message_id=5,
            company_name="ACME TRADERS",
            base_rows=_base_rows(),
            file_id="tg-file-3",
            active=active,
        )

    pending_q.create.assert_awaited_once()
    kwargs = pending_q.create.await_args.kwargs
    assert kwargs["extraction_document_type"] == wk._PENDING_KIND_TALLY
    assert kwargs["extraction"]["vouchers"][0]["dedup_key"] == "hash-1"
    assert kwargs["file_ids"] == ["tg-file-3"]
    pending_q.attach_prompt_message.assert_awaited_once_with(pending_row.id, 77)


async def test_resume_dispatches_tally_ingestion() -> None:
    client = SimpleNamespace(id=uuid.uuid4(), trade_name="S.S Traders")
    row = SimpleNamespace(
        id=uuid.uuid4(),
        resolved_client_id=client.id,
        message_id=5,
        file_ids=["tg-file-3"],
        extraction={"vouchers": wk._tally_rows_to_json(_base_rows())},
        extraction_document_type=wk._PENDING_KIND_TALLY,
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
        patch.object(wk, "_tally_ingest_for_client", AsyncMock()) as ingest,
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
    assert kwargs["base_rows"][0]["occurred_at"] == datetime(2026, 1, 2, tzinfo=UTC)
    assert kwargs["base_rows"][0]["source"] == "tally"
