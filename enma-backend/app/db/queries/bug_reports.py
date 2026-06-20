"""Bug-report CRUD — captured via ``/bug`` and surfaced to operators."""

from __future__ import annotations

from typing import cast

from app.db.models.bug_report import BugReport
from app.db.queries.base import BaseQuery


class BugReportQuery(BaseQuery):
    """Tenant-scoped bug-report operations."""

    async def record(
        self,
        *,
        chat_id: int | None,
        body: str,
        severity: str = "unspecified",
    ) -> BugReport:
        """Append one CA-reported bug. Plain-text body, no parsing."""
        row = BugReport(chat_id=chat_id, body=body, severity=severity)
        return cast(BugReport, await self._insert(row))


__all__ = ["BugReportQuery"]
