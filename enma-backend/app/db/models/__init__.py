"""ORM model registry.

Importing this module ensures all models are registered with
``Base.metadata`` — required for Alembic autogenerate and for
``Base.metadata.create_all()`` in tests.
"""

from app.db.models.brain_event import BrainEvent
from app.db.models.bug_report import BugReport
from app.db.models.client import Client
from app.db.models.conversation import Conversation
from app.db.models.document import Document
from app.db.models.filing import FilingApproval
from app.db.models.firm import CaFirm, FirmUser
from app.db.models.infrastructure import (
    ClientNotification,
    IdempotencyLog,
    IdentityResolutionLog,
)
from app.db.models.outcome_unit import OutcomeUnit
from app.db.models.pending_assignment import PendingAssignment
from app.db.models.reconciliation_run import ReconciliationRun
from app.db.models.rule import CaFirmRule
from app.db.models.tally_export_run import TallyExportRun
from app.db.models.task import Task
from app.db.models.trajectory import AgenticTrajectory
from app.db.models.verdict_correction import VerdictCorrection

__all__ = [
    "AgenticTrajectory",
    "BrainEvent",
    "BugReport",
    "CaFirm",
    "CaFirmRule",
    "Client",
    "ClientNotification",
    "Conversation",
    "Document",
    "FilingApproval",
    "FirmUser",
    "IdempotencyLog",
    "IdentityResolutionLog",
    "OutcomeUnit",
    "PendingAssignment",
    "ReconciliationRun",
    "TallyExportRun",
    "Task",
    "VerdictCorrection",
]
