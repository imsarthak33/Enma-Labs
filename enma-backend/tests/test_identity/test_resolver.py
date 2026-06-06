"""Tests for the 5-stage identity resolver.

The resolver is pure orchestration over SQLAlchemy queries; we drive
each stage in isolation by mocking the helper functions and then run
the full cascade once to confirm the wiring.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from app.identity.resolver import (
    ResolutionConfidence,
    ResolutionStage,
    resolve_identity,
)


def _patch_audit() -> Any:
    """Replace ``_emit`` so we don't need a DB to record audit rows."""

    async def _fake_emit(*, stage: Any, confidence: Any, client_id: Any, pending_id: Any, **_: Any):
        from app.identity.resolver import ResolutionOutcome

        return ResolutionOutcome(
            stage=stage,
            confidence=confidence,
            client_id=client_id,
            pending_assignment_id=pending_id,
            resolution_time_ms=1,
            log_id=uuid.uuid4(),
        )

    return patch("app.identity.resolver._emit", new=_fake_emit)


class TestCascade:
    @pytest.mark.asyncio
    async def test_session_wins_first(self) -> None:
        firm = uuid.uuid4()
        winner = uuid.uuid4()
        with (
            _patch_audit(),
            patch(
                "app.identity.resolver._resolve_from_session",
                new=AsyncMock(return_value=winner),
            ),
            patch(
                "app.identity.resolver._resolve_from_caption", new=AsyncMock()
            ) as cap,
            patch(
                "app.identity.resolver._resolve_from_gstin", new=AsyncMock()
            ) as gstin,
        ):
            out = await resolve_identity(
                session=AsyncMock(),
                ca_firm_id=firm,
                chat_id=1,
                caption="ABC Corp",
                extracted_text=None,
                extracted_vendor_gstin=None,
                file_ids=["f1"],
            )
        assert out.stage is ResolutionStage.SESSION
        assert out.confidence is ResolutionConfidence.HIGH
        assert out.client_id == winner
        cap.assert_not_called()
        gstin.assert_not_called()

    @pytest.mark.asyncio
    async def test_caption_then_gstin(self) -> None:
        firm = uuid.uuid4()
        cap_winner = uuid.uuid4()
        with (
            _patch_audit(),
            patch(
                "app.identity.resolver._resolve_from_session",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.identity.resolver._resolve_from_caption",
                new=AsyncMock(
                    return_value=(cap_winner, ResolutionConfidence.HIGH)
                ),
            ),
            patch(
                "app.identity.resolver._resolve_from_gstin", new=AsyncMock()
            ) as gstin,
        ):
            out = await resolve_identity(
                session=AsyncMock(),
                ca_firm_id=firm,
                chat_id=1,
                caption="ABC Corp",
                extracted_text=None,
                extracted_vendor_gstin=None,
                file_ids=["f1"],
            )
        assert out.stage is ResolutionStage.CAPTION
        assert out.client_id == cap_winner
        gstin.assert_not_called()

    @pytest.mark.asyncio
    async def test_gstin_match(self) -> None:
        firm = uuid.uuid4()
        winner = uuid.uuid4()
        with (
            _patch_audit(),
            patch(
                "app.identity.resolver._resolve_from_session",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.identity.resolver._resolve_from_caption",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.identity.resolver._resolve_from_gstin",
                new=AsyncMock(return_value=winner),
            ),
            patch(
                "app.identity.resolver._resolve_from_vendor_history",
                new=AsyncMock(),
            ) as vh,
        ):
            out = await resolve_identity(
                session=AsyncMock(),
                ca_firm_id=firm,
                chat_id=1,
                caption=None,
                extracted_text="GSTIN: 27AABCU9603R1ZP",
                extracted_vendor_gstin=None,
                file_ids=["f1"],
            )
        assert out.stage is ResolutionStage.GSTIN
        assert out.client_id == winner
        vh.assert_not_called()

    @pytest.mark.asyncio
    async def test_vendor_history_match(self) -> None:
        firm = uuid.uuid4()
        winner = uuid.uuid4()
        with (
            _patch_audit(),
            patch(
                "app.identity.resolver._resolve_from_session",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.identity.resolver._resolve_from_caption",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.identity.resolver._resolve_from_gstin",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.identity.resolver._resolve_from_vendor_history",
                new=AsyncMock(return_value=winner),
            ),
        ):
            out = await resolve_identity(
                session=AsyncMock(),
                ca_firm_id=firm,
                chat_id=1,
                caption=None,
                extracted_text=None,
                extracted_vendor_gstin="29AADCB2230M1ZT",
                file_ids=["f1"],
            )
        assert out.stage is ResolutionStage.VENDOR
        assert out.confidence is ResolutionConfidence.MEDIUM
        assert out.client_id == winner

    @pytest.mark.asyncio
    async def test_explicit_ask_when_no_signal(self) -> None:
        firm = uuid.uuid4()
        pending = uuid.uuid4()
        with (
            _patch_audit(),
            patch(
                "app.identity.resolver._resolve_from_session",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.identity.resolver._resolve_from_caption",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.identity.resolver._resolve_from_gstin",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.identity.resolver._resolve_from_vendor_history",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.identity.resolver._queue_pending",
                new=AsyncMock(return_value=pending),
            ),
        ):
            out = await resolve_identity(
                session=AsyncMock(),
                ca_firm_id=firm,
                chat_id=1,
                caption=None,
                extracted_text=None,
                extracted_vendor_gstin=None,
                file_ids=["f1"],
            )
        assert out.stage is ResolutionStage.EXPLICIT_ASK
        assert out.client_id is None
        assert out.pending_assignment_id == pending
        assert out.is_resolved is False
