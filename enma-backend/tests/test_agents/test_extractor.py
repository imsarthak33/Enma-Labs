"""Tests for the extractor agent.

LLM is monkey-patched.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.agents import extractor as extractor_module
from app.agents.extractor import ExtractorError, extract_document
from app.services.llm import ChatResponse, LLMRole


def _valid_extraction() -> dict[str, Any]:
    return {
        "vendor": {"name": "V", "gstin": "29AAAGU0010P1Z5"},
        "buyer": {"name": "B", "gstin": "27AABCU9603R1ZN"},
        "invoice_number": "INV-1",
        "invoice_date": "2026-04-01",
        "line_items": [
            {
                "description": "x",
                "taxable_value": "100.00",
                "cgst_rate": "9",
                "cgst_amount": "9.00",
                "sgst_rate": "9",
                "sgst_amount": "9.00",
            }
        ],
        "totals": {
            "taxable_value": "100.00",
            "total_cgst": "9.00",
            "total_sgst": "9.00",
            "total_igst": "0.00",
            "grand_total": "118.00",
        },
    }


def _patch_llm(monkeypatch: pytest.MonkeyPatch, content: str) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    async def fake_call_chat(role: LLMRole, **kwargs: Any) -> ChatResponse:
        captured.append({"role": role, **kwargs})
        return ChatResponse(
            content=content,
            model="ext-model",
            input_tokens=123,
            output_tokens=45,
            raw={},
        )

    monkeypatch.setattr(extractor_module.llm, "call_chat", fake_call_chat)
    return captured


@pytest.mark.asyncio
async def test_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, json.dumps(_valid_extraction()))
    result = await extract_document(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, document_type="B2B_INVOICE"
    )
    assert result.document_type == "B2B_INVOICE"
    assert result.data["vendor"]["gstin"] == "29AAAGU0010P1Z5"
    assert result.input_tokens == 123
    assert result.model_name == "ext-model"


@pytest.mark.asyncio
async def test_uses_extraction_role_and_correct_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_llm(monkeypatch, json.dumps(_valid_extraction()))
    await extract_document(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, document_type="FREIGHT")
    assert captured[0]["role"] is LLMRole.EXTRACTION
    # The system message should include FREIGHT-specific law context.
    sys_msg = captured[0]["messages"][0]["content"]
    assert "FREIGHT" in sys_msg
    assert "Reverse Charge" in sys_msg or "GTA" in sys_msg


@pytest.mark.asyncio
async def test_non_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, "no json")
    with pytest.raises(ExtractorError, match="non-JSON"):
        await extract_document(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, document_type="B2B_INVOICE")


@pytest.mark.asyncio
async def test_array_payload_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, json.dumps([{"k": "v"}]))
    with pytest.raises(ExtractorError, match="non-object"):
        await extract_document(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, document_type="B2B_INVOICE")


@pytest.mark.asyncio
async def test_missing_required_keys_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    partial = _valid_extraction()
    del partial["totals"]
    _patch_llm(monkeypatch, json.dumps(partial))
    with pytest.raises(ExtractorError, match="totals"):
        await extract_document(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, document_type="B2B_INVOICE")


@pytest.mark.asyncio
async def test_pdf_input_uses_file_content_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refactor H: PDF input must build a ``file`` part, not ``image_url``."""
    captured = _patch_llm(monkeypatch, json.dumps(_valid_extraction()))
    pdf_bytes = b"%PDF-1.4\n%\x00\x00\x00\x00" + b"\x00" * 32
    await extract_document(pdf_bytes, document_type="B2B_INVOICE")
    parts = captured[0]["messages"][1]["content"]
    content_types = [p["type"] for p in parts]
    assert "file" in content_types
    file_part = next(p for p in parts if p["type"] == "file")
    assert file_part["file"]["file_data"].startswith("data:application/pdf;base64,")


@pytest.mark.asyncio
async def test_image_input_still_uses_image_url_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_llm(monkeypatch, json.dumps(_valid_extraction()))
    await extract_document(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, document_type="B2B_INVOICE")
    parts = captured[0]["messages"][1]["content"]
    content_types = [p["type"] for p in parts]
    assert "image_url" in content_types
    assert "file" not in content_types
