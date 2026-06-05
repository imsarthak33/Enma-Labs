"""ORM model registry.

Importing this module ensures all models are registered with
``Base.metadata`` — required for Alembic autogenerate and for
``Base.metadata.create_all()`` in tests.
"""

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
from app.db.models.rule import CaFirmRule
from app.db.models.task import Task

__all__ = [
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
    "Task",
]
