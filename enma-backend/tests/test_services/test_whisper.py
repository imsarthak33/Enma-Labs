"""Tests for the Whisper transcription client."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from app.services import whisper


class _Resp:
    status_code = 200
    text = "ok"

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


@pytest.fixture
def _configured() -> Any:
    with patch.object(whisper.settings, "whisper_endpoint", new="https://w.example/v1"):
        yield


class TestTranscribeOgg:
    @pytest.mark.asyncio
    async def test_happy_path(self, _configured: Any) -> None:
        client = AsyncMock()
        client.post = AsyncMock(
            return_value=_Resp({"text": "  show overdue tasks  ", "model": "whisper-1"})
        )
        result = await whisper.transcribe_ogg(b"OggS" + b"\x00" * 16, client=client)
        assert result.text == "show overdue tasks"
        assert result.model == "whisper-1"

    @pytest.mark.asyncio
    async def test_not_configured(self) -> None:
        with (
            patch.object(whisper.settings, "whisper_endpoint", new=None),
            pytest.raises(whisper.WhisperNotConfigured),
        ):
            await whisper.transcribe_ogg(b"x")

    @pytest.mark.asyncio
    async def test_empty_payload(self, _configured: Any) -> None:
        with pytest.raises(whisper.WhisperError, match="empty audio"):
            await whisper.transcribe_ogg(b"")

    @pytest.mark.asyncio
    async def test_oversize_payload(self, _configured: Any) -> None:
        big = b"x" * (whisper.MAX_VOICE_BYTES + 1)
        with pytest.raises(whisper.WhisperError, match="too large"):
            await whisper.transcribe_ogg(big)

    @pytest.mark.asyncio
    async def test_non_2xx_raises(self, _configured: Any) -> None:
        bad = _Resp({})
        bad.status_code = 500
        client = AsyncMock()
        client.post = AsyncMock(return_value=bad)
        with pytest.raises(whisper.WhisperError, match="HTTP 500"):
            await whisper.transcribe_ogg(b"OggS\x00", client=client)

    @pytest.mark.asyncio
    async def test_missing_text_field(self, _configured: Any) -> None:
        client = AsyncMock()
        client.post = AsyncMock(return_value=_Resp({"model": "whisper-1"}))
        with pytest.raises(whisper.WhisperError, match="missing 'text'"):
            await whisper.transcribe_ogg(b"OggS\x00", client=client)
