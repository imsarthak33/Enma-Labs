"""Tests for the Phase 7 ``_run_voice_pipeline`` background coroutine."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from app.api.middleware.envelope_verify import ENVELOPE_VERSION, DecodedEnvelope
from app.api.routes import worker as worker_module
from app.services import telegram as telegram_service
from app.services import whisper as whisper_service


def _voice_envelope(*, file_id: str | None = "ogg-1") -> DecodedEnvelope:
    payload: dict[str, Any] = {}
    if file_id is not None:
        payload["file_id"] = file_id
    return DecodedEnvelope(
        v=ENVELOPE_VERSION,
        kind="voice",
        issued_at=datetime.now(UTC),
        nonce="n",
        chat_id=42,
        message_id=1,
        update_id=1,
        payload=payload,
    )


@pytest.mark.asyncio
async def test_happy_path_echoes_then_runs_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sends: list[Any] = []
    command_calls: list[Any] = []

    async def _download(_fid: str) -> bytes:
        return b"OggS" + b"\x00" * 16

    async def _transcribe(_b: bytes, **_k: Any) -> whisper_service.TranscriptionResult:
        return whisper_service.TranscriptionResult(text="list overdue tasks", model="whisper-1")

    async def _send(**kw: Any) -> dict[str, Any]:
        sends.append(kw)
        return {}

    async def _run_command(env: DecodedEnvelope) -> None:
        command_calls.append(env)

    monkeypatch.setattr(telegram_service, "download_file", _download)
    monkeypatch.setattr(whisper_service, "transcribe_ogg", _transcribe)
    monkeypatch.setattr(telegram_service, "send_message", _send)
    monkeypatch.setattr(worker_module, "_run_command_pipeline", _run_command)

    await worker_module._run_voice_pipeline(_voice_envelope())

    # One echo message sent, then the command pipeline gets a synthetic envelope.
    assert any("Heard:" in s["html_text"] for s in sends)
    assert len(command_calls) == 1
    synth = command_calls[0]
    assert synth.kind == "command"
    assert synth.payload["text"] == "list overdue tasks"


@pytest.mark.asyncio
async def test_whisper_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    sends: list[Any] = []

    async def _download(_fid: str) -> bytes:
        return b"OggS"

    async def _transcribe(_b: bytes, **_k: Any) -> Any:
        raise whisper_service.WhisperNotConfigured("nope")

    async def _send(**kw: Any) -> dict[str, Any]:
        sends.append(kw)
        return {}

    monkeypatch.setattr(telegram_service, "download_file", _download)
    monkeypatch.setattr(whisper_service, "transcribe_ogg", _transcribe)
    monkeypatch.setattr(telegram_service, "send_message", _send)

    cmd_mock = AsyncMock()
    monkeypatch.setattr(worker_module, "_run_command_pipeline", cmd_mock)

    await worker_module._run_voice_pipeline(_voice_envelope())
    assert any("not enabled" in s["html_text"] for s in sends)
    cmd_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_transcribe_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    sends: list[Any] = []

    async def _download(_fid: str) -> bytes:
        return b"OggS"

    async def _transcribe(_b: bytes, **_k: Any) -> Any:
        raise whisper_service.WhisperError("upstream")

    async def _send(**kw: Any) -> dict[str, Any]:
        sends.append(kw)
        return {}

    monkeypatch.setattr(telegram_service, "download_file", _download)
    monkeypatch.setattr(whisper_service, "transcribe_ogg", _transcribe)
    monkeypatch.setattr(telegram_service, "send_message", _send)

    cmd_mock = AsyncMock()
    monkeypatch.setattr(worker_module, "_run_command_pipeline", cmd_mock)

    await worker_module._run_voice_pipeline(_voice_envelope())
    assert any("Could not transcribe" in s["html_text"] for s in sends)
    cmd_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_file_id(monkeypatch: pytest.MonkeyPatch) -> None:
    sends: list[Any] = []

    async def _send(**kw: Any) -> dict[str, Any]:
        sends.append(kw)
        return {}

    monkeypatch.setattr(telegram_service, "send_message", _send)
    await worker_module._run_voice_pipeline(_voice_envelope(file_id=None))
    assert any("Could not find a voice note" in s["html_text"] for s in sends)


@pytest.mark.asyncio
async def test_empty_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    sends: list[Any] = []

    async def _download(_fid: str) -> bytes:
        return b"OggS"

    async def _transcribe(_b: bytes, **_k: Any) -> whisper_service.TranscriptionResult:
        return whisper_service.TranscriptionResult(text="   ", model=None)

    async def _send(**kw: Any) -> dict[str, Any]:
        sends.append(kw)
        return {}

    monkeypatch.setattr(telegram_service, "download_file", _download)
    monkeypatch.setattr(whisper_service, "transcribe_ogg", _transcribe)
    monkeypatch.setattr(telegram_service, "send_message", _send)
    cmd_mock = AsyncMock()
    monkeypatch.setattr(worker_module, "_run_command_pipeline", cmd_mock)

    await worker_module._run_voice_pipeline(_voice_envelope())
    assert any("empty" in s["html_text"].lower() for s in sends)
    cmd_mock.assert_not_awaited()
