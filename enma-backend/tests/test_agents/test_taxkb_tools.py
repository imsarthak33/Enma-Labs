"""Tax-KB advice tool tests — gst_rate_lookup + tds_section_lookup.

The tools are thin deterministic wrappers over the in-memory lookups and
don't touch ``ctx``, so we drive them with a dummy context.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.agents.supervisor import _tool_gst_rate_lookup, _tool_tds_section_lookup

_CTX = SimpleNamespace()


@pytest.mark.asyncio
async def test_gst_rate_lookup_hit() -> None:
    out = await _tool_gst_rate_lookup(_CTX, {"hsn": "5201"})
    assert out["found"] is True
    assert out["gst_rate"] == "5"
    assert "cotton" in out["description"].lower()


@pytest.mark.asyncio
async def test_gst_rate_lookup_longest_prefix() -> None:
    # 9963H18 (hotel > 7500) should beat a shorter 9963 prefix.
    out = await _tool_gst_rate_lookup(_CTX, {"hsn": "9963H18"})
    assert out["found"] is True
    assert out["gst_rate"] == "18"


@pytest.mark.asyncio
async def test_gst_rate_lookup_miss() -> None:
    out = await _tool_gst_rate_lookup(_CTX, {"hsn": "ZZZZ"})
    assert out["found"] is False


@pytest.mark.asyncio
async def test_gst_rate_lookup_requires_hsn() -> None:
    from app.agents.supervisor import ToolError

    with pytest.raises(ToolError):
        await _tool_gst_rate_lookup(_CTX, {})


@pytest.mark.asyncio
async def test_tds_new_act_after_transition() -> None:
    out = await _tool_tds_section_lookup(
        _CTX, {"nature_of_payment": "contractor", "payment_date": "2026-06-24"}
    )
    assert out["found"] is True
    assert "393" in out["section_reference"]
    assert out["act"] == "Income Tax Act 2025"


@pytest.mark.asyncio
async def test_tds_legacy_act_before_transition() -> None:
    out = await _tool_tds_section_lookup(
        _CTX, {"nature_of_payment": "contractor", "payment_date": "2025-12-01"}
    )
    assert out["found"] is True
    assert out["legacy_section"] == "194C"
    assert out["act"] == "Income Tax Act 1961"


@pytest.mark.asyncio
async def test_tds_requires_nature() -> None:
    from app.agents.supervisor import ToolError

    with pytest.raises(ToolError):
        await _tool_tds_section_lookup(_CTX, {})
