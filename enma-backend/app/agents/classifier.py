"""Document classifier agent.

Takes the raw bytes of an image (or PDF first page) and asks the layout
model to assign one of :data:`DOCUMENT_TYPES`. The output is parsed into
a strict Pydantic model so downstream code never sees a hallucinated
type string.

The classifier is intentionally lightweight (small model, narrow prompt)
because every uploaded document hits it before the heavier extractor.
"""

from __future__ import annotations

import json
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.logging_setup import get_logger
from app.prompts.master_prompt import build_classifier_prompt
from app.prompts.tax_law_library import DOCUMENT_TYPES
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
    "ClassificationResult",
    "ClassifierError",
    "classify_document",
]


# Max tokens for the classification response. The schema is < 100 tokens
# of JSON; giving it 256 leaves plenty of headroom.
_MAX_TOKENS: Final[int] = 256


_ALLOWED_CONFIDENCE: Final[frozenset[str]] = frozenset({"HIGH", "MEDIUM", "LOW"})


class ClassifierError(RuntimeError):
    """Raised when classification fails (LLM error, schema violation, etc.)."""


class ClassificationResult(BaseModel):
    """Strict-shape output from the classifier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_type: str = Field(..., description="One of DOCUMENT_TYPES.")
    confidence: str = Field(..., description="HIGH | MEDIUM | LOW.")
    reasoning: str = Field(..., max_length=400)

    @field_validator("document_type")
    @classmethod
    def _document_type_in_enum(cls, v: str) -> str:
        if v not in DOCUMENT_TYPES:
            raise ValueError(f"document_type must be one of {DOCUMENT_TYPES}, got {v!r}")
        return v

    @field_validator("confidence")
    @classmethod
    def _confidence_in_set(cls, v: str) -> str:
        if v not in _ALLOWED_CONFIDENCE:
            raise ValueError(f"confidence must be one of {sorted(_ALLOWED_CONFIDENCE)}, got {v!r}")
        return v


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def classify_document(document_bytes: bytes) -> ClassificationResult:
    """Classify ``document_bytes`` (single image or single-page PDF) by type.

    ``document_bytes`` may be either:

    * Raw image bytes (PNG / JPEG / WebP) — passed via ``image_url`` part.
    * A single-page PDF (``%PDF-`` magic) — passed via the OpenAI ``file``
      part. The vision LLMs we use (NVIDIA NIM nemotron-ocr, gpt-4o)
      accept PDF pages natively, so we don't rasterise.

    Always returns a :class:`ClassificationResult`. On any failure that
    isn't catastrophic (e.g., LLM returns a non-enum type), we fall back
    to ``UNKNOWN`` with confidence ``LOW`` so the pipeline keeps moving
    and the CA can correct.
    """
    kind = detect_kind(document_bytes)
    if is_image(kind):
        content_part = build_image_content(document_bytes)
    elif kind is FileKind.PDF:
        content_part = build_pdf_content(document_bytes)
    else:
        # Unknown kind — let the image builder raise its detailed MIME error.
        content_part = build_image_content(document_bytes)
    messages: list[ChatMessage] = [
        {"role": "system", "content": build_classifier_prompt()},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Classify the attached document."},
                content_part,
            ],
        },
    ]

    response = await llm.call_chat(
        LLMRole.LAYOUT,
        messages=messages,
        response_format={"type": "json_object"},
        max_tokens=_MAX_TOKENS,
    )

    try:
        payload = json.loads(response.content)
    except json.JSONDecodeError as exc:
        _log.warning("classifier_non_json", body=response.content[:200])
        raise ClassifierError(f"classifier returned non-JSON content: {exc}") from exc

    try:
        return ClassificationResult.model_validate(payload)
    except ValidationError as exc:
        _log.warning(
            "classifier_schema_violation",
            errors=[e["msg"] for e in exc.errors()[:5]],
            payload=payload,
        )
        # Soft-fall back to UNKNOWN so the pipeline still produces a result.
        return ClassificationResult(
            document_type="UNKNOWN",
            confidence="LOW",
            reasoning=f"schema violation: {exc.errors()[0]['msg'][:200]}",
        )
