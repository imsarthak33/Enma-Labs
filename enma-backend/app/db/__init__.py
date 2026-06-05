"""Database layer.

Phase 1: models, base query, and session are all available.

- ``Base`` — declarative base for all ORM models
- ``get_session`` — FastAPI dependency for async sessions
- ``BaseQuery`` — tenant-scoped query base class
"""

from app.db.base import Base
from app.db.queries.base import BaseQuery
from app.db.session import (
    dispose_engine,
    get_engine,
    get_session,
    get_sessionmaker,
)

__all__ = [
    "Base",
    "BaseQuery",
    "dispose_engine",
    "get_engine",
    "get_session",
    "get_sessionmaker",
]
