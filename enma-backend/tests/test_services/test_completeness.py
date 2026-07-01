"""Tests for the Phase 8b completeness ledger.

The three legs (books / GSTR-2B / bank) are derived from the existing
DocumentQuery + BrainEventQuery, so we fake those query classes and assert
the leg presence + is_complete/missing roll-ups.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from app.services import completeness as cmod
from app.services.completeness import (
    LEG_BANK,
    LEG_BOOKS,
    LEG_GSTR2B,
    assess_completeness,
    assess_firm_completeness,
)

FIRM_ID = uuid.uuid4()
CLIENT_ID = uuid.uuid4()


def _doc(client_id: uuid.UUID, status: str = "completed") -> SimpleNamespace:
    return SimpleNamespace(client_id=client_id, processing_status=status)


def _event(payload: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(payload=payload)


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    docs: list[Any],
    gstr2b: list[Any],
    bank: list[Any],
    active_clients: list[Any] | None = None,
) -> None:
    """Patch the three query classes the service constructs."""

    class _FakeDocumentQuery:
        def __init__(self, **_kw: Any) -> None: ...

        async def list_by_filing_period(self, *, year: int, month: int) -> list[Any]:
            return docs

    class _FakeBrainEventQuery:
        def __init__(self, **_kw: Any) -> None: ...

        async def list_filtered(
            self, *, client_id: Any, source: str, event_type: str, limit: int
        ) -> list[Any]:
            if source == "gstn_portal":
                return gstr2b
            if source == "bank":
                return bank[:limit]
            return []

    class _FakeClientQuery:
        def __init__(self, **_kw: Any) -> None: ...

        async def list_active(self) -> list[Any]:
            return active_clients or []

    monkeypatch.setattr(cmod, "DocumentQuery", _FakeDocumentQuery)
    monkeypatch.setattr(cmod, "BrainEventQuery", _FakeBrainEventQuery)
    monkeypatch.setattr(cmod, "ClientQuery", _FakeClientQuery)


async def _assess(**over: Any) -> Any:
    return await assess_completeness(
        session=object(),
        ca_firm_id=FIRM_ID,
        client_id=CLIENT_ID,
        trade_name="S.S Traders",
        month=over.get("month", 3),
        year=over.get("year", 2026),
    )


@pytest.mark.asyncio
async def test_all_three_legs_present_is_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fakes(
        monkeypatch,
        docs=[_doc(CLIENT_ID)],
        gstr2b=[_event({"return_period": "032026"})],
        bank=[_event({"amount": "100"})],
    )
    report = await _assess()
    assert report.is_complete is True
    assert report.missing == []
    assert {leg.leg: leg.present for leg in report.legs} == {
        LEG_BOOKS: True,
        LEG_GSTR2B: True,
        LEG_BANK: True,
    }


@pytest.mark.asyncio
async def test_missing_legs_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    # Books present, GSTR-2B + bank absent.
    _install_fakes(monkeypatch, docs=[_doc(CLIENT_ID)], gstr2b=[], bank=[])
    report = await _assess()
    assert report.is_complete is False
    assert report.missing == [LEG_GSTR2B, LEG_BANK]


@pytest.mark.asyncio
async def test_books_ignores_other_clients_and_non_exportable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = uuid.uuid4()
    _install_fakes(
        monkeypatch,
        docs=[
            _doc(other),  # different client
            _doc(CLIENT_ID, status="processing"),  # not exportable
        ],
        gstr2b=[_event({"return_period": "032026"})],
        bank=[_event({})],
    )
    report = await _assess()
    books = next(leg for leg in report.legs if leg.leg == LEG_BOOKS)
    assert books.present is False and books.count == 0
    assert report.missing == [LEG_BOOKS]


@pytest.mark.asyncio
async def test_gstr2b_filters_on_return_period(monkeypatch: pytest.MonkeyPatch) -> None:
    # 2B rows exist but for a different return period → not counted for March.
    _install_fakes(
        monkeypatch,
        docs=[_doc(CLIENT_ID)],
        gstr2b=[_event({"return_period": "022026"}), _event({"return_period": "042026"})],
        bank=[_event({})],
    )
    report = await _assess(month=3, year=2026)
    gstr2b = next(leg for leg in report.legs if leg.leg == LEG_GSTR2B)
    assert gstr2b.present is False
    assert report.missing == [LEG_GSTR2B]


@pytest.mark.asyncio
async def test_get_completeness_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """The supervisor tool resolves the client, defaults the period, and
    returns per-leg presence + a human-readable missing list."""
    from unittest.mock import AsyncMock, patch

    from app.agents.supervisor import SupervisorContext, _tool_get_completeness
    from app.services.completeness import ClientCompleteness, LegStatus

    cid = uuid.uuid4()
    client = SimpleNamespace(id=cid, trade_name="S.S Traders")
    report = ClientCompleteness(
        client_id=cid,
        trade_name="S.S Traders",
        month=3,
        year=2026,
        legs=(
            LegStatus(leg=LEG_BOOKS, present=True, count=4),
            LegStatus(leg=LEG_GSTR2B, present=False, count=0),
            LegStatus(leg=LEG_BANK, present=False, count=0),
        ),
    )
    ctx = SupervisorContext(session=AsyncMock(), ca_firm_id=FIRM_ID, chat_id=1)
    with (
        patch(
            "app.agents.supervisor._resolve_client_from_args",
            AsyncMock(return_value=client),
        ),
        patch(
            "app.agents.supervisor.assess_completeness",
            AsyncMock(return_value=report),
        ),
    ):
        res = await _tool_get_completeness(
            ctx,
            {"client_name": "S.S Traders", "filing_period_month": 3, "filing_period_year": 2026},
        )
    assert res["client"] == "S.S Traders"
    assert res["complete"] is False
    assert res["filing_period_month"] == 3
    assert [leg["present"] for leg in res["legs"]] == [True, False, False]
    # Missing legs rendered with human labels, not bare ids.
    assert res["missing"] == ["GSTR-2B (ITC)", "Bank statement"]


@pytest.mark.asyncio
async def test_firm_sweep_covers_every_active_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c1 = SimpleNamespace(id=uuid.uuid4(), trade_name="Alpha")
    c2 = SimpleNamespace(id=uuid.uuid4(), trade_name="Beta")
    _install_fakes(
        monkeypatch,
        docs=[],  # nobody has books
        gstr2b=[],
        bank=[],
        active_clients=[c1, c2],
    )
    reports = await assess_firm_completeness(
        session=object(), ca_firm_id=FIRM_ID, month=3, year=2026
    )
    assert [r.trade_name for r in reports] == ["Alpha", "Beta"]
    assert all(not r.is_complete for r in reports)
    assert all(len(r.missing) == 3 for r in reports)
