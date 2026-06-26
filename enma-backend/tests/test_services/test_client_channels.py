"""Tests — per-client channel helpers + the get_client_ingest_setup tool (8a)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.client_channels import (
    client_deep_link,
    client_ingest_email,
    parse_client_link_payload,
)


def test_deep_link_round_trips_to_client_id() -> None:
    cid = uuid.uuid4()
    link = client_deep_link(cid)
    assert link == f"https://t.me/enmalabsbot?start=client_{cid}"
    payload = link.split("start=")[1]
    assert parse_client_link_payload(payload) == cid


def test_ingest_email() -> None:
    cid = uuid.uuid4()
    assert client_ingest_email(cid) == f"client-{cid}@ingest.enmalabs.in"


def test_parse_rejects_non_client_payloads() -> None:
    assert parse_client_link_payload(str(uuid.uuid4())) is None  # bare firm UUID
    assert parse_client_link_payload("client_not-a-uuid") is None
    assert parse_client_link_payload("random") is None
    assert parse_client_link_payload("") is None


async def test_get_client_ingest_setup_tool() -> None:
    from app.agents.supervisor import SupervisorContext, _tool_get_client_ingest_setup

    cid = uuid.uuid4()
    client = SimpleNamespace(id=cid, trade_name="S.S Traders", telegram_chat_id=None)
    ctx = SupervisorContext(session=AsyncMock(), ca_firm_id=uuid.uuid4(), chat_id=1)
    with patch(
        "app.agents.supervisor._resolve_client_from_args",
        AsyncMock(return_value=client),
    ):
        res = await _tool_get_client_ingest_setup(ctx, {"client_name": "S.S Traders"})
    assert res["client"] == "S.S Traders"
    assert res["telegram_deep_link"].endswith(f"start=client_{cid}")
    assert res["ingest_email"] == f"client-{cid}@ingest.enmalabs.in"
    assert res["telegram_connected"] is False
