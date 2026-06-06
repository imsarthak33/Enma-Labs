"""Tests for the filing-approval regex, snapshot, and finalize flow.

The regex and snapshot builder are pure functions and tested without a
database. The end-to-end parse → finalize loop uses a sqlite-backed
fixture sketched in :mod:`tests.test_security.test_tenant_isolation`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from app.agents.filing_approval import (
    APPROVAL_REGEX,
    InvalidApprovalString,
    build_filing_snapshot,
    parse_approval_intent,
)


class TestApprovalRegex:
    @pytest.mark.parametrize(
        "text",
        [
            "ENMA APPROVE FILING January 2026",
            "ENMA APPROVE FILING March 2026",
            "ENMA APPROVE FILING December 2099",
        ],
    )
    def test_valid_match(self, text: str) -> None:
        assert APPROVAL_REGEX.fullmatch(text) is not None

    @pytest.mark.parametrize(
        "text",
        [
            # Wrong case.
            "enma approve filing March 2026",
            "ENMA approve FILING March 2026",
            # Wrong month casing.
            "ENMA APPROVE FILING march 2026",
            # Missing year.
            "ENMA APPROVE FILING March",
            # Extra space.
            "ENMA APPROVE FILING  March 2026",
            # Lowercase year letters / non-4-digit.
            "ENMA APPROVE FILING March 26",
            # Wrong verb.
            "ENMA APPROVED FILING March 2026",
            # Surrounding text.
            "please ENMA APPROVE FILING March 2026",
            "ENMA APPROVE FILING March 2026 please",
            # Invented month.
            "ENMA APPROVE FILING Smarch 2026",
        ],
    )
    def test_invalid(self, text: str) -> None:
        assert APPROVAL_REGEX.fullmatch(text) is None


class TestSnapshotBuilder:
    def test_empty_period_snapshot(self) -> None:
        firm = uuid.uuid4()
        snap = build_filing_snapshot(
            ca_firm_id=firm, month=3, year=2026, documents=[]
        )
        assert snap["ca_firm_id"] == str(firm)
        assert snap["document_count"] == 0
        assert snap["document_ids"] == []
        assert snap["totals"]["taxable_value"] == "0"
        assert snap["totals"]["total_tax"] == "0"
        assert snap["period_label"] == "March 2026"

    def test_aggregates_decimals_safely(self) -> None:
        from unittest.mock import MagicMock

        firm = uuid.uuid4()
        client = uuid.uuid4()
        doc1 = MagicMock()
        doc1.id = uuid.uuid4()
        doc1.client_id = client
        doc1.extraction_data = {
            "totals": {
                "taxable_value": "1000.10",
                "total_cgst": "90.00",
                "total_sgst": "90.00",
                "total_igst": "0",
            }
        }
        doc2 = MagicMock()
        doc2.id = uuid.uuid4()
        doc2.client_id = client
        doc2.extraction_data = {
            "totals": {
                "taxable_value": "500.50",
                "total_cgst": "0",
                "total_sgst": "0",
                "total_igst": "90.09",
            }
        }
        snap = build_filing_snapshot(
            ca_firm_id=firm,
            month=3,
            year=2026,
            documents=[doc1, doc2],
        )
        assert snap["document_count"] == 2
        assert Decimal(snap["totals"]["taxable_value"]) == Decimal("1500.60")
        assert Decimal(snap["totals"]["total_tax"]) == Decimal("270.09")
        assert snap["per_client"][str(client)]["document_count"] == "2"

    def test_doc_ids_sorted_for_determinism(self) -> None:
        from unittest.mock import MagicMock

        firm = uuid.uuid4()
        client = uuid.uuid4()
        ids = sorted([uuid.uuid4() for _ in range(3)])
        docs = []
        for i in reversed(ids):  # feed in reverse order
            m = MagicMock()
            m.id = i
            m.client_id = client
            m.extraction_data = {"totals": {}}
            docs.append(m)
        snap = build_filing_snapshot(
            ca_firm_id=firm, month=3, year=2026, documents=docs
        )
        assert snap["document_ids"] == [str(i) for i in ids]


class TestParseRaisesOnInvalid:
    @pytest.mark.asyncio
    async def test_invalid_string_raises(self) -> None:
        with pytest.raises(InvalidApprovalString):
            await parse_approval_intent(
                session=None,  # type: ignore[arg-type]
                ca_firm_id=uuid.uuid4(),
                text="approve filing march 2026",
            )
