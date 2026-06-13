"""Tests for the classifier agent.

We monkey-patch :func:`app.services.llm.call_chat` so we drive the
classifier with deterministic responses and check shape handling.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.agents import classifier as classifier_module
from app.agents.classifier import ClassifierError, classify_document
from app.services.llm import ChatResponse, LLMRole


def _patch_llm(monkeypatch: pytest.MonkeyPatch, content: str) -> list[dict[str, Any]]:
    """Patch call_chat with a stub that captures invocations."""
    captured: list[dict[str, Any]] = []

    async def fake_call_chat(role: LLMRole, **kwargs: Any) -> ChatResponse:
        captured.append({"role": role, **kwargs})
        return ChatResponse(
            content=content,
            model="test-model",
            input_tokens=10,
            output_tokens=5,
            raw={},
        )

    monkeypatch.setattr(classifier_module.llm, "call_chat", fake_call_chat)
    return captured


@pytest.mark.asyncio
async def test_happy_path_returns_validated_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "document_type": "B2B_INVOICE",
        "confidence": "HIGH",
        "reasoning": "Vendor and buyer both have visible GSTINs.",
    }
    _patch_llm(monkeypatch, json.dumps(payload))
    result = await classify_document(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    assert result.document_type == "B2B_INVOICE"
    assert result.confidence == "HIGH"
    assert result.reasoning.startswith("Vendor and buyer")


@pytest.mark.asyncio
async def test_uses_layout_role_and_json_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_llm(
        monkeypatch,
        json.dumps({"document_type": "RESTAURANT", "confidence": "MEDIUM", "reasoning": "x"}),
    )
    await classify_document(b"\xff\xd8\xff" + b"\x00" * 16)
    assert captured[0]["role"] is LLMRole.LAYOUT
    assert captured[0]["response_format"] == {"type": "json_object"}
    # System message contains the classifier prompt.
    system_msg = captured[0]["messages"][0]
    assert system_msg["role"] == "system"
    assert "CLASSIFY DOCUMENT TYPE" in system_msg["content"]


@pytest.mark.asyncio
async def test_invalid_json_raises_classifier_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_llm(monkeypatch, "this is not json at all")
    with pytest.raises(ClassifierError, match="non-JSON"):
        await classify_document(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)


@pytest.mark.asyncio
async def test_pdf_input_uses_file_content_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refactor H: PDF input must build a ``file`` part, not ``image_url``.

    Production regression — the pipeline's _prepare_image_bytes returns
    single-page PDF bytes for PDFs; the classifier previously passed those
    bytes to ``build_image_content`` which only knows PNG/JPEG/WebP magic,
    so every PDF upload died with HTTP 400 "could not detect image MIME"
    during the classification stage.
    """
    captured = _patch_llm(
        monkeypatch,
        json.dumps({"document_type": "B2B_INVOICE", "confidence": "HIGH", "reasoning": "ok"}),
    )
    # Minimal valid-looking PDF: %PDF- magic plus padding.
    pdf_bytes = b"%PDF-1.4\n%\x00\x00\x00\x00" + b"\x00" * 32
    await classify_document(pdf_bytes)
    user_msg = captured[0]["messages"][1]
    parts = user_msg["content"]
    content_types = [p["type"] for p in parts]
    assert "file" in content_types, (
        f"expected a ``file`` content part for PDF input, got types={content_types}"
    )
    file_part = next(p for p in parts if p["type"] == "file")
    assert file_part["file"]["file_data"].startswith("data:application/pdf;base64,")


@pytest.mark.asyncio
async def test_image_input_still_uses_image_url_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: the image path must NOT regress to the file shape."""
    captured = _patch_llm(
        monkeypatch,
        json.dumps({"document_type": "B2B_INVOICE", "confidence": "HIGH", "reasoning": "ok"}),
    )
    await classify_document(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    parts = captured[0]["messages"][1]["content"]
    content_types = [p["type"] for p in parts]
    assert "image_url" in content_types
    assert "file" not in content_types


@pytest.mark.asyncio
async def test_unknown_type_falls_back_to_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_llm(
        monkeypatch,
        json.dumps(
            {
                "document_type": "TOTALLY_MADE_UP",
                "confidence": "HIGH",
                "reasoning": "x",
            }
        ),
    )
    result = await classify_document(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    assert result.document_type == "UNKNOWN"
    assert result.confidence == "LOW"


@pytest.mark.asyncio
async def test_invalid_confidence_falls_back_to_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_llm(
        monkeypatch,
        json.dumps(
            {
                "document_type": "B2B_INVOICE",
                "confidence": "VERY_HIGH",  # not in enum
                "reasoning": "x",
            }
        ),
    )
    result = await classify_document(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    assert result.document_type == "UNKNOWN"


@pytest.mark.asyncio
async def test_image_part_attached_to_user_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_llm(
        monkeypatch,
        json.dumps({"document_type": "FREIGHT", "confidence": "MEDIUM", "reasoning": "x"}),
    )
    await classify_document(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    user_msg = captured[0]["messages"][1]
    assert user_msg["role"] == "user"
    assert isinstance(user_msg["content"], list)
    types = [part["type"] for part in user_msg["content"]]
    assert "text" in types
    assert "image_url" in types
