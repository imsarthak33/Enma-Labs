"""Whisper transcription client — OGG → text for voice notes.

The endpoint is configurable (``settings.whisper_endpoint``) so any
OpenAI-compatible audio-transcription server works (OpenAI, Deepgram via
LiteLLM, a self-hosted vLLM with audio adapters, …). We POST to that
URL with ``multipart/form-data`` and read back the transcript.

Token-usage logging mirrors ``services.llm`` so the Phase 8 dashboard
can include voice transcription in the cost view (where the upstream
endpoint reports usage; OpenAI's transcription endpoint does not, so we
log byte-size as a proxy).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import httpx
import sentry_sdk

from app.config import settings
from app.logging_setup import get_logger

_log = get_logger(__name__)

__all__ = [
    "MAX_VOICE_BYTES",
    "MAX_VOICE_SECONDS",
    "TranscriptionResult",
    "WhisperError",
    "transcribe_ogg",
]


# Telegram caps voice notes at 60s; we accept ≤1 MiB as a belt-and-braces
# byte-length ceiling (OGG @ 16kbps mono is ~120 KiB / minute, so 1 MiB
# covers ~8 minutes of audio if Telegram ever raises the limit).
MAX_VOICE_SECONDS: Final[int] = 60
MAX_VOICE_BYTES: Final[int] = 1 * 1024 * 1024


@dataclass(frozen=True)
class TranscriptionResult:
    """The transcript + the upstream model name (for cost logging)."""

    text: str
    model: str | None


class WhisperError(RuntimeError):
    """Raised on transcription failure — caller falls back to error message."""


class WhisperNotConfigured(WhisperError):
    """Distinct subtype so tests/configurations can assert it."""


async def transcribe_ogg(
    ogg_bytes: bytes,
    *,
    client: httpx.AsyncClient | None = None,
    language: str | None = "en",
) -> TranscriptionResult:
    """POST the OGG bytes to the configured Whisper endpoint.

    The endpoint is expected to accept the OpenAI ``audio/transcriptions``
    multipart shape:

        POST {endpoint}
        Content-Type: multipart/form-data
        fields:
          file:  <audio bytes, filename="voice.ogg">
          model: <configured model name>  (omitted if unset upstream)
          language: <ISO-639-1 hint>

    Returns a :class:`TranscriptionResult`; raises :class:`WhisperError`
    on any failure.
    """
    if settings.whisper_endpoint is None:
        raise WhisperNotConfigured("whisper endpoint is not configured")
    if not ogg_bytes:
        raise WhisperError("empty audio payload")
    if len(ogg_bytes) > MAX_VOICE_BYTES:
        raise WhisperError(
            f"audio too large: {len(ogg_bytes)} > {MAX_VOICE_BYTES} bytes"
        )

    files = {"file": ("voice.ogg", ogg_bytes, "audio/ogg")}
    data: dict[str, str] = {}
    if language:
        data["language"] = language
    # Model field — many providers (OpenAI, Groq) require it; some
    # (self-hosted Whisper) ignore it. We pass a safe default; deployers
    # who run a different model override via env.
    data["model"] = "whisper-1"

    headers: dict[str, str] = {}
    if settings.llm_api_key is not None:
        # Reuse the LLM API key by convention; deployers who run
        # separate auth set a different value via env-overrides later.
        headers["Authorization"] = (
            f"Bearer {settings.llm_api_key.get_secret_value()}"
        )

    own_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    try:
        try:
            resp = await http.post(
                settings.whisper_endpoint,
                files=files,
                data=data,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            sentry_sdk.capture_exception(exc)
            _log.error("whisper_request_failed", error=str(exc))
            raise WhisperError(f"whisper request failed: {exc}") from exc

        if resp.status_code != httpx.codes.OK:
            _log.error(
                "whisper_non_2xx",
                status=resp.status_code,
                body=resp.text[:300],
            )
            raise WhisperError(f"whisper HTTP {resp.status_code}")

        try:
            body = resp.json()
        except ValueError as exc:
            raise WhisperError(f"whisper response not JSON: {exc}") from exc
    finally:
        if own_client:
            await http.aclose()

    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str):
        raise WhisperError(f"whisper response missing 'text': {body!r}")
    model = body.get("model") if isinstance(body, dict) else None

    _log.info(
        "whisper_transcribed",
        bytes=len(ogg_bytes),
        text_len=len(text),
        model=model,
    )
    return TranscriptionResult(text=text.strip(), model=model if isinstance(model, str) else None)
