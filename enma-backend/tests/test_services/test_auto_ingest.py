"""Tests — acquisition-agnostic auto-ingest seam (Phase 7a)."""

from __future__ import annotations

import json
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


# ── GSTR-2B auto-ingest (GSP pull) ─────────────────────────────────────────


def _2b_json(recipient_gstin: str) -> bytes:
    return json.dumps(
        {
            "data": {
                "rtnprd": "032026",
                "gstin": recipient_gstin,
                "docdata": {
                    "b2b": [
                        {
                            "ctin": "29AAACW1234F1ZX",
                            "trdnm": "Acme",
                            "inv": [
                                {
                                    "inum": "INV-1",
                                    "dt": "17-03-2026",
                                    "val": 1180,
                                    "itcavl": "Y",
                                    "items": [
                                        {
                                            "txval": 1000,
                                            "cgst": 90,
                                            "sgst": 90,
                                            "igst": 0,
                                            "cess": 0,
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                },
            }
        }
    ).encode("utf-8")


async def _run_2b(*, file_bytes: bytes, client: SimpleNamespace, firm: SimpleNamespace):
    brain = MagicMock()
    brain.record_dedup_bulk = AsyncMock(return_value=(1, 0))
    deliver = AsyncMock(return_value=SimpleNamespace(summary="2b reconciled"))
    with (
        patch.object(auto_ingest, "BrainEventQuery", return_value=brain),
        patch.object(auto_ingest.recon_runner, "reconcile_and_deliver", deliver),
    ):
        res = await auto_ingest.ingest_gstr2b_bytes(
            session=AsyncMock(), firm=firm, client=client, file_bytes=file_bytes
        )
    res._deliver = deliver  # type: ignore[attr-defined]
    return res


async def test_ingests_gstr2b_for_matching_client() -> None:
    client = SimpleNamespace(id=uuid.uuid4(), gstin="27AABCC1234D1Z5", trade_name="X")
    res = await _run_2b(file_bytes=_2b_json("27AABCC1234D1Z5"), client=client, firm=_firm())
    assert res.ingested is True
    assert res.summary == "2b reconciled"
    res._deliver.assert_awaited_once()  # type: ignore[attr-defined]


async def test_gstr2b_recipient_mismatch_is_rejected() -> None:
    # The 2B is addressed to a different GSTIN than the client → never ingest.
    client = SimpleNamespace(id=uuid.uuid4(), gstin="27AABCC1234D1Z5", trade_name="X")
    res = await _run_2b(file_bytes=_2b_json("99ZZZZZ0000Z1Z9"), client=client, firm=_firm())
    assert res.ingested is False
    res._deliver.assert_not_awaited()  # type: ignore[attr-defined]
