"""Unified LLM client — OpenAI-compatible Chat Completions surface.

Why a single thin wrapper instead of the official SDK?

  * The official SDK pulls a heavy dependency tree we don't need.
  * Every agent calls one of three model "roles" (layout / extraction /
    reasoning) — a three-way dispatch is the entire surface.
  * Centralising the HTTP call gives us one place to log token usage,
    capture errors to Sentry, and enforce the per-call ``max_tokens``
    ceiling.

The wire surface
----------------
We POST to the configured ``*_MODEL_ENDPOINT`` URL with an
OpenAI-style chat-completions request body. Every server we care about
(OpenAI itself, vLLM, NVIDIA NIM, LiteLLM proxy, …) supports this.

Vision messages
---------------
Multimodal content is passed via the OpenAI message-content array shape:

    {"role": "user", "content": [
        {"type": "text", "text": "..."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
    ]}

Helpers in this module build the right shape from raw image / PDF bytes.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx
import sentry_sdk

from app.config import settings
from app.logging_setup import get_logger

_log = get_logger(__name__)

__all__ = [
    "ChatMessage",
    "ChatResponse",
    "LLMError",
    "LLMRole",
    "NoEndpointConfigured",
    "build_image_content",
    "build_pdf_content",
    "call_chat",
    "close_client",
    "get_client",
]


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class LLMRole(StrEnum):
    """Which configured endpoint a call should land on."""

    LAYOUT = "layout"
    EXTRACTION = "extraction"
    REASONING = "reasoning"


ChatMessage = dict[str, Any]
"""A chat message in OpenAI shape: {role, content}. ``content`` is either
a string or a list of typed content parts (text/image_url)."""


@dataclass(frozen=True)
class ChatResponse:
    """Structured response from a chat-completions call."""

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    raw: dict[str, Any]


class LLMError(RuntimeError):
    """Raised when the LLM call fails (HTTP error, malformed response, etc.)."""


class NoEndpointConfigured(LLMError):
    """Raised when an endpoint URL is empty — distinct so tests can assert it."""


# ---------------------------------------------------------------------------
# HTTP client lifecycle
# ---------------------------------------------------------------------------


_client: httpx.AsyncClient | None = None


def _build_client() -> httpx.AsyncClient:
    timeout = httpx.Timeout(
        connect=10.0,
        read=float(settings.llm_request_timeout_s),
        write=30.0,
        pool=10.0,
    )
    return httpx.AsyncClient(timeout=timeout)


def get_client() -> httpx.AsyncClient:
    """Return the process-wide async LLM client, creating it on first call."""
    global _client  # noqa: PLW0603 — intentional process-singleton
    if _client is None:
        _client = _build_client()
    return _client


async def close_client() -> None:
    """Close the shared client. Called from the lifespan shutdown."""
    global _client  # noqa: PLW0603 — intentional process-singleton
    if _client is not None:
        await _client.aclose()
        _client = None


# ---------------------------------------------------------------------------
# Role → endpoint / model resolution
# ---------------------------------------------------------------------------


def _resolve_role(role: LLMRole) -> tuple[str, str]:
    """Return ``(endpoint_url, model_name)`` for ``role``."""
    if role is LLMRole.LAYOUT:
        return settings.layout_model_endpoint, settings.layout_model_name
    if role is LLMRole.EXTRACTION:
        return settings.extraction_model_endpoint, settings.extraction_model_name
    if role is LLMRole.REASONING:
        return settings.reasoning_model_endpoint, settings.reasoning_model_name
    raise LLMError(f"unknown LLM role: {role!r}")  # pragma: no cover


def _auth_header() -> dict[str, str]:
    if settings.llm_api_key is None:
        return {}
    return {"Authorization": f"Bearer {settings.llm_api_key.get_secret_value()}"}


# ---------------------------------------------------------------------------
# Multimodal content builders
# ---------------------------------------------------------------------------


_IMAGE_MIME_BY_BYTES = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
}


def build_image_content(image_bytes: bytes, *, mime: str | None = None) -> dict[str, Any]:
    """Build an ``image_url`` content part from raw image bytes."""
    detected: str | None = mime
    if detected is None:
        for magic, m in _IMAGE_MIME_BY_BYTES.items():
            if image_bytes.startswith(magic):
                detected = m
                break
        if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
            detected = "image/webp"
    if detected is None:
        raise LLMError("could not detect image MIME — pass mime= explicitly")
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{detected};base64,{b64}"},
    }


def build_pdf_content(pdf_bytes: bytes) -> dict[str, Any]:
    """Build a PDF content part (OpenAI's ``input_file`` shape).

    Not every provider supports PDF natively; for those, the pipeline
    splits to pages and rasterises before calling this builder. Phase 4
    only ships the helper; the pipeline decides whether to use it based
    on the configured model.
    """
    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    return {
        "type": "file",
        "file": {
            "filename": "document.pdf",
            "file_data": f"data:application/pdf;base64,{b64}",
        },
    }


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


async def call_chat(
    role: LLMRole,
    messages: list[ChatMessage],
    *,
    response_format: dict[str, str] | None = None,
    temperature: float = 0.0,
    max_tokens: int | None = None,
    extra_body: dict[str, Any] | None = None,
    client: httpx.AsyncClient | None = None,
) -> ChatResponse:
    """Call a chat-completions endpoint for the given role.

    ``response_format={"type": "json_object"}`` is the agent-pipeline
    standard; pass it explicitly per call site so each agent's contract
    is visible in the diff.

    Token usage is emitted as a structured log line (``llm_token_usage``)
    on every successful call. Errors are captured to Sentry.
    """
    endpoint, model = _resolve_role(role)
    if not endpoint:
        raise NoEndpointConfigured(f"no endpoint configured for role {role.value}")

    ceiling = settings.llm_max_output_tokens
    capped = ceiling if max_tokens is None else min(max_tokens, ceiling)

    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": capped,
    }
    if response_format is not None:
        body["response_format"] = response_format
    if extra_body:
        body.update(extra_body)

    http = client or get_client()
    headers = {"Content-Type": "application/json", **_auth_header()}

    try:
        resp = await http.post(endpoint, json=body, headers=headers)
    except httpx.HTTPError as exc:
        _log.error(
            "llm_request_failed",
            role=role.value,
            model=model,
            error=str(exc),
        )
        sentry_sdk.capture_exception(exc)
        raise LLMError(f"LLM request failed: {exc}") from exc

    if resp.status_code != httpx.codes.OK:
        _log.error(
            "llm_non_2xx",
            role=role.value,
            model=model,
            status=resp.status_code,
            body=resp.text[:500],
        )
        raise LLMError(f"LLM returned HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        data: dict[str, Any] = resp.json()
    except ValueError as exc:
        raise LLMError(f"LLM response was not JSON: {exc}") from exc

    return _parse_response(data, role=role, model=model)


def _parse_response(data: dict[str, Any], *, role: LLMRole, model: str) -> ChatResponse:
    """Extract content + usage from a Chat Completions response body."""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError(f"LLM response missing 'choices': {data}")
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    if not isinstance(message, dict):
        raise LLMError(f"LLM choice has no 'message': {first}")
    content = message.get("content")
    if not isinstance(content, str):
        raise LLMError(f"LLM message content not a string: {content!r}")

    usage = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
    input_tokens = int(usage.get("prompt_tokens", 0))
    output_tokens = int(usage.get("completion_tokens", 0))

    _log.info(
        "llm_token_usage",
        role=role.value,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )

    return ChatResponse(
        content=content,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        raw=data,
    )
