"""Structured-field extractor agent.

Takes the same input as the classifier (image bytes) plus the
classification's document_type, and asks the EXTRACTION model to emit a
JSON object matching the extractor schema (see master_prompt).

The result is a single ``ExtractionResult`` whose ``data`` field is the
raw decoded JSON dict. We do NOT translate the dict into Pydantic
sub-models for every field — the verifier wants the freedom to operate
on the dict-of-strings shape that comes off the wire, and Phase 5's tax
engine prefers it too.

What this agent DOES validate:
  * Response parses as JSON.
  * Top-level shape includes the canonical sections (vendor, buyer,
    line_items, totals).

What it does NOT validate:
  * Numeric correctness — that's the verifier's job.
  * GSTIN checksum — verifier's job.
  * Document-type-specific business rules — Phase 5 tax engine.
"""

from __future__ import annotations

import json
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from app.logging_setup import get_logger
from app.prompts.master_prompt import build_extractor_prompt
from app.services import llm
from app.services.file_processor import FileKind, detect_kind, is_image
from app.services.llm import (
    ChatMessage,
    LLMRole,
    build_image_content,
    build_pdf_content,
)

_log = get_logger(__name__)

__all__ = [
    "ExtractionResult",
    "ExtractorError",
    "extract_document",
]


# Extractor responses can be large (multi-line invoices). Cap at 4096
# tokens — enough for ~50 line items.
_MAX_TOKENS: Final[int] = 4096


_REQUIRED_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset(
    # ``totals`` was required when the LLM was responsible for computing
    # the canonical totals. After O it's populated by the Python
    # reconciler from ``observed_totals`` + ``line_items``, so we drop
    # it from the required set. Either ``observed_totals`` (preferred)
    # or ``line_items`` is enough for the reconciler to produce an
    # answer; missing both is still an extraction failure.
    {"vendor", "buyer", "line_items"}
)


class ExtractorError(RuntimeError):
    """Raised when extraction fails irrecoverably (LLM error, bad JSON)."""


class ExtractionResult(BaseModel):
    """Wrapper around the raw extraction dict + observability metadata."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    document_type: str = Field(..., description="Echoes the classifier's choice.")
    data: dict[str, Any] = Field(..., description="Raw decoded extraction JSON.")
    model_name: str
    input_tokens: int
    output_tokens: int


def _parse_json_object(content: str) -> dict[str, Any]:
    """Parse a JSON object from an extractor response, tolerantly.

    NIM vision models vary in how they emit JSON: some honour
    ``response_format=json_object``, others wrap the object in a ```json
    markdown fence or add a sentence around it. We try the raw content first,
    then fall back to the outermost ``{...}`` span (which transparently handles
    both fences and surrounding prose). Raises ``ExtractorError`` if no object
    can be recovered.
    """
    text = (content or "").strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        obj = None
    else:
        # Content was valid JSON. A dict is the happy path; any other top-level
        # type (e.g. a list) is a real shape error — surface it, don't dig in.
        if isinstance(obj, dict):
            return obj
        raise ExtractorError(f"extractor returned non-object payload: {type(obj).__name__}")

    # Not clean JSON — a vision model likely wrapped the object in a ```json
    # fence or surrounding prose. Recover the outermost {...} span.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            span = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            span = None
        if isinstance(span, dict):
            return span
    raise ExtractorError(f"extractor returned non-JSON content: {text[:200]!r}")


async def extract_document(
    document_bytes: bytes,
    *,
    document_type: str,
) -> ExtractionResult:
    """Run the extractor on ``document_bytes`` (single image or PDF page).

    ``document_type`` is the classifier's choice; we route the relevant
    law-context paragraph into the prompt so the model has the right
    framing (e.g., restaurant bills get the blocked-credit caveat).

    Content-part selection matches :func:`classify_document` — PDF pages
    go in as ``file`` parts, images as ``image_url`` parts.
    """
    kind = detect_kind(document_bytes)
    if is_image(kind):
        content_part = build_image_content(document_bytes)
    elif kind is FileKind.PDF:
        content_part = build_pdf_content(document_bytes)
    else:
        content_part = build_image_content(document_bytes)
    messages: list[ChatMessage] = [
        {"role": "system", "content": build_extractor_prompt(document_type)},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"Extract structured fields from this {document_type} document.",
                },
                content_part,
            ],
        },
    ]

    # NB: no response_format=json_object here. NIM vision models handle JSON
    # mode inconsistently — nano-vl 500s on it, llama-3.2-11b ignores it. We ask
    # for JSON in the prompt and parse tolerantly (see _parse_json_object), which
    # works across the vision models and lets us pick on capability/latency.
    response = await llm.call_chat(
        LLMRole.EXTRACTION,
        messages=messages,
        max_tokens=_MAX_TOKENS,
    )

    try:
        payload = _parse_json_object(response.content)
    except ExtractorError:
        _log.warning("extractor_non_json", body=(response.content or "")[:400])
        raise

    missing = _REQUIRED_TOP_LEVEL_KEYS - set(payload.keys())
    if missing:
        raise ExtractorError(f"extractor payload missing required keys: {sorted(missing)}")

    return ExtractionResult(
        document_type=document_type,
        data=payload,
        model_name=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )
