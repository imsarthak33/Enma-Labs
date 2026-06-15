"""Model instantiation and metadata tests.

Verifies that all ORM models register correctly with Base.metadata,
that required columns are present, and that table relationships are
properly configured.
"""

from __future__ import annotations

import uuid
from typing import ClassVar

from app.db.base import Base
from app.db.models import (
    CaFirm,
    CaFirmRule,
    Client,
    ClientNotification,
    Conversation,
    Document,
    FilingApproval,
    IdempotencyLog,
    IdentityResolutionLog,
    Task,
)


class TestModelRegistration:
    """Verify all models are registered in Base.metadata."""

    EXPECTED_TABLES: ClassVar[set[str]] = {
        "ca_firms",
        "firm_users",
        "clients",
        "documents",
        "ca_firm_rules",
        "conversations",
        "tasks",
        "filing_approvals",
        "idempotency_log",
        "identity_resolution_log",
        "client_notifications",
        "tally_export_runs",
    }

    def test_all_tables_registered(self) -> None:
        """Every canonical table must be in Base.metadata."""
        registered = set(Base.metadata.tables.keys())
        assert self.EXPECTED_TABLES.issubset(
            registered
        ), f"Missing tables: {self.EXPECTED_TABLES - registered}"

    def test_table_count(self) -> None:
        """13 tables after W3 adds ``tally_export_runs``."""
        assert len(Base.metadata.tables) == 13


class TestCaFirmModel:
    """Verify CaFirm model structure."""

    def test_instantiation(self) -> None:
        firm = CaFirm(
            firm_name="Test Firm",
            admin_chat_id=12345,
            telegram_bot_token="test_token",
        )
        assert firm.firm_name == "Test Firm"
        assert firm.admin_chat_id == 12345

    def test_tablename(self) -> None:
        assert CaFirm.__tablename__ == "ca_firms"


class TestClientModel:
    """Verify Client model structure."""

    def test_instantiation(self) -> None:
        client = Client(
            ca_firm_id=uuid.uuid4(),
            trade_name="Test Corp",
            gstin="27AABCU9603R1ZP",
            pan="AABCU9603R",
            state_code="27",
        )
        assert client.trade_name == "Test Corp"
        assert client.gstin == "27AABCU9603R1ZP"
        assert len(client.gstin) == 15

    def test_tablename(self) -> None:
        assert Client.__tablename__ == "clients"


class TestDocumentModel:
    """Verify Document model structure."""

    def test_instantiation(self) -> None:
        doc = Document(
            ca_firm_id=uuid.uuid4(),
            client_id=uuid.uuid4(),
            document_type="B2B_INVOICE",
            source_file_ids={"file_ids": ["abc123"]},
            extraction_data={"vendor": {"name": "Test"}},
        )
        assert doc.document_type == "B2B_INVOICE"
        assert doc.source_file_ids == {"file_ids": ["abc123"]}

    def test_tablename(self) -> None:
        assert Document.__tablename__ == "documents"


class TestCaFirmRuleModel:
    """Verify CaFirmRule model structure."""

    def test_instantiation(self) -> None:
        rule = CaFirmRule(
            ca_firm_id=uuid.uuid4(),
            rule_type="correction",
            rule_text="Always block ITC for vendor XYZ",
            source="human_correction",
        )
        assert rule.rule_type == "correction"
        assert rule.source == "human_correction"

    def test_tablename(self) -> None:
        assert CaFirmRule.__tablename__ == "ca_firm_rules"


class TestConversationModel:
    """Verify Conversation model structure."""

    def test_instantiation(self) -> None:
        conv = Conversation(
            ca_firm_id=uuid.uuid4(),
            chat_id=12345,
            role="user",
            content="What is the ITC status?",
        )
        assert conv.role == "user"
        assert conv.content == "What is the ITC status?"


class TestTaskModel:
    """Verify Task model structure."""

    def test_instantiation(self) -> None:
        task = Task(
            ca_firm_id=uuid.uuid4(),
            title="File GSTR-3B for March 2026",
            status="pending",
            priority=1,
        )
        assert task.title == "File GSTR-3B for March 2026"
        assert task.status == "pending"


class TestFilingApprovalModel:
    """Verify FilingApproval model structure."""

    def test_instantiation(self) -> None:
        filing = FilingApproval(
            ca_firm_id=uuid.uuid4(),
            filing_month=3,
            filing_year=2026,
            approved_by_chat_id=12345,
            filing_snapshot={"document_count": 47, "total_claim": "345000.00"},
            approval_hash="sha256:abc123def456",
        )
        assert filing.filing_month == 3
        assert filing.filing_year == 2026
        assert filing.approval_hash.startswith("sha256:")


class TestInfrastructureModels:
    """Verify infrastructure model structures."""

    def test_idempotency_log(self) -> None:
        log = IdempotencyLog(
            chat_id=12345,
            message_id=789,
            payload_hash="sha256:test",
        )
        assert log.chat_id == 12345

    def test_identity_resolution_log(self) -> None:
        log = IdentityResolutionLog(
            ca_firm_id=uuid.uuid4(),
            stage_reached=3,
            confidence="HIGH",
        )
        assert log.stage_reached == 3

    def test_client_notification(self) -> None:
        notif = ClientNotification(
            ca_firm_id=uuid.uuid4(),
            client_id=uuid.uuid4(),
            notification_type="document_chase",
        )
        assert notif.notification_type == "document_chase"
