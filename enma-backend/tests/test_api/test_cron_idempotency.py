"""Tests for the cron-envelope dedup key (ADR-007 §Decision 2).

The synthetic key is ``(chat_id=0, message_id=floor(scheduled_at_s/60))``.
We unit-test :func:`_cron_dedup_key` directly so we don't need a live
backend to exercise the wiring.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.api.middleware.envelope_verify import ENVELOPE_VERSION, DecodedEnvelope
from app.api.middleware.idempotency import CRON_CHAT_ID, _cron_dedup_key


def _cron_envelope(*, scheduled_at: str | None = "2026-06-06T03:30:00+00:00") -> DecodedEnvelope:
    payload: dict[str, object] = {}
    if scheduled_at is not None:
        payload["scheduled_at"] = scheduled_at
    return DecodedEnvelope(
        v=ENVELOPE_VERSION,
        kind="cron_morning_briefing",
        issued_at=datetime(2026, 6, 6, tzinfo=UTC),
        nonce="n",
        chat_id=None,
        message_id=None,
        update_id=None,
        payload=payload,
    )


class TestCronDedupKey:
    def test_same_minute_collapses(self) -> None:
        a = _cron_dedup_key(_cron_envelope(scheduled_at="2026-06-06T03:30:00+00:00"))
        b = _cron_dedup_key(_cron_envelope(scheduled_at="2026-06-06T03:30:45+00:00"))
        assert a == b
        assert a[0] == CRON_CHAT_ID

    def test_next_minute_distinct(self) -> None:
        a = _cron_dedup_key(_cron_envelope(scheduled_at="2026-06-06T03:30:00+00:00"))
        b = _cron_dedup_key(_cron_envelope(scheduled_at="2026-06-06T03:31:00+00:00"))
        assert a != b

    def test_naive_timestamp_treated_as_utc(self) -> None:
        # The cron verifier rejects this in practice; the dedup helper
        # tolerates it defensively for self-trigger / replay scenarios.
        a = _cron_dedup_key(_cron_envelope(scheduled_at="2026-06-06T03:30:00"))
        b = _cron_dedup_key(_cron_envelope(scheduled_at="2026-06-06T03:30:00+00:00"))
        assert a == b

    def test_missing_field_raises_400(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _cron_dedup_key(_cron_envelope(scheduled_at=None))
        assert exc_info.value.status_code == 400

    def test_invalid_format_raises_400(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _cron_dedup_key(_cron_envelope(scheduled_at="not-a-date"))
        assert exc_info.value.status_code == 400
