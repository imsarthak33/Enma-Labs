"""A5 Brain-Surface summary tests — pure-function rollup of brain_events.

``_summarize_brain_events`` is deterministic and DB-free, so we exercise
it directly with lightweight stand-in event objects shaped like
``BrainEvent`` rows (the attributes the summary reads: source,
event_type, occurred_at, payload).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from app.agents.supervisor import _party_amount, _summarize_brain_events


def _voucher(
    *,
    number: str,
    day: int,
    party_amount: str,
    event_type: str = "voucher_purchase",
) -> SimpleNamespace:
    return SimpleNamespace(
        source="tally",
        event_type=event_type,
        occurred_at=datetime(2025, 8, day, tzinfo=UTC),
        payload={
            "voucher_number": number,
            "narration": f"Invoice {number}",
            "ledger_entries": [
                {"ledger_name": "CLEIND", "amount": party_amount, "is_party": True},
                {"ledger_name": "Purchase @ 18%", "amount": "-1000.00", "is_party": False},
            ],
        },
    )


def test_party_amount_extracts_grand_total() -> None:
    payload: dict[str, Any] = {
        "ledger_entries": [
            {"ledger_name": "X", "amount": "36020.00", "is_party": True},
            {"ledger_name": "Purchase", "amount": "-30000.00", "is_party": False},
        ]
    }
    assert str(_party_amount(payload)) == "36020.00"


def test_party_amount_none_when_no_party_entry() -> None:
    assert _party_amount({"ledger_entries": [{"amount": "1.00", "is_party": False}]}) is None
    assert _party_amount({}) is None


def test_summary_counts_and_totals() -> None:
    events = [
        _voucher(number="127", day=17, party_amount="36020.00"),
        _voucher(number="123", day=15, party_amount="27140.00"),
        _voucher(number="120", day=6, party_amount="9750.00"),
    ]
    summary = _summarize_brain_events(events)
    assert summary["count"] == 3
    assert summary["by_source"] == {"tally": 3}
    assert summary["by_event_type"] == {"voucher_purchase": 3}
    # 36020 + 27140 + 9750 = 72910.00 — Decimal sum, string output.
    assert summary["total_party_value"] == "72910.00"
    assert summary["valued_event_count"] == 3
    assert summary["earliest"] == "2025-08-06"
    assert summary["latest"] == "2025-08-17"
    assert len(summary["samples"]) == 3


def test_summary_empty() -> None:
    summary = _summarize_brain_events([])
    assert summary["count"] == 0
    assert summary["total_party_value"] is None
    assert summary["earliest"] is None
    assert summary["samples"] == []


def test_summary_caps_samples_at_ten() -> None:
    events = [_voucher(number=str(i), day=(i % 28) + 1, party_amount="100.00") for i in range(25)]
    summary = _summarize_brain_events(events)
    assert summary["count"] == 25
    assert len(summary["samples"]) == 10
    assert summary["total_party_value"] == "2500.00"


def test_summary_mixed_sources_some_unvalued() -> None:
    events = [
        _voucher(number="1", day=1, party_amount="500.00"),
        SimpleNamespace(
            source="gmail",
            event_type="email_received",
            occurred_at=datetime(2025, 8, 20, tzinfo=UTC),
            payload={"subject": "GST notice"},  # no ledger entries → unvalued
        ),
    ]
    summary = _summarize_brain_events(events)
    assert summary["count"] == 2
    assert summary["by_source"] == {"tally": 1, "gmail": 1}
    assert summary["valued_event_count"] == 1
    assert summary["total_party_value"] == "500.00"
