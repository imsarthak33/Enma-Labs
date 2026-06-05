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
from app.services.llm import ChatMessage, LLMRole, build_image_content

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
    {"vendor", "buyer", "line_items", "totals"}
)


class ExtractorError(RuntimeError):
    """Raised when extraction fails irrecoverably (LLM error, bad JSON)."""


class ExtractionResult(BaseModel):
    """Wrapper around the raw extraction dict + observability metadata."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    document_type: str = Field(..., description="Echoes the classifier's choice.")
    data: dict[str, Any] = Field(
        ..., description="Raw decoded extraction JSON."
    )
    model_name: str
    input_tokens: int
    output_tokens: int


async def extract_document(
    image_bytes: bytes,
    *,
    document_type: str,
) -> ExtractionResult:
    """Run the extractor on ``image_bytes``.

    ``document_type`` is the classifier's choice; we route the relevant
    law-context paragraph into the prompt so the model has the right
    framing (e.g., restaurant bills get the blocked-credit caveat).
    """
    image_part = build_image_content(image_bytes)
    messages: list[ChatMessage] = [
        {"role": "system", "content": build_extractor_prompt(document_type)},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Extract structured fields from this "
                        f"{document_type} document."
                    ),
                },
                image_part,
            ],
        },
    ]

    response = await llm.call_chat(
        LLMRole.EXTRACTION,
        messages=messages,
        response_format={"type": "json_object"},
        max_tokens=_MAX_TOKENS,
    )

    try:
        payload = json.loads(response.content)
    except json.JSONDecodeError as exc:
        _log.warning("extractor_non_json", body=response.content[:400])
        raise ExtractorError(
            f"extractor returned non-JSON content: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise ExtractorError(
            f"extractor returned non-object payload: {type(payload).__name__}"
        )

    missing = _REQUIRED_TOP_LEVEL_KEYS - set(payload.keys())
    if missing:
        raise ExtractorError(
            f"extractor payload missing required keys: {sorted(missing)}"
        )

    return ExtractionResult(
        document_type=document_type,
        data=payload,
        model_name=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )
