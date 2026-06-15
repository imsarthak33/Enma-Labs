"""Tally export audit-log writes/reads — tenant scoped via BaseQuery.

Writes are append-only by design. The model has no UPDATE path; the
audit row is the immutable record of one ``export_to_tally`` call.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import cast

from app.db.models.tally_export_run import TallyExportRun
from app.db.queries.base import BaseQuery


class TallyExportQuery(BaseQuery):
    """Tenant-scoped Tally export-audit operations."""

    async def create(
        self,
        *,
        client_id: uuid.UUID | str,
        filing_month: int,
        filing_year: int,
        voucher_count: int,
        file_sha256: str,
        exported_by_chat_id: int,
    ) -> TallyExportRun:
        """Insert one audit row for a completed export."""
        cid = client_id if isinstance(client_id, uuid.UUID) else uuid.UUID(str(client_id))
        row = TallyExportRun(
            client_id=cid,
            filing_month=filing_month,
            filing_year=filing_year,
            voucher_count=voucher_count,
            file_sha256=file_sha256,
            exported_by_chat_id=exported_by_chat_id,
        )
        return cast(TallyExportRun, await self._insert(row))

    async def list_for_period(
        self,
        *,
        client_id: uuid.UUID | str,
        filing_month: int,
        filing_year: int,
    ) -> Sequence[TallyExportRun]:
        """Return all prior exports for (client, month, year), newest first.

        Used by the supervisor tool to tell the CA "you already exported
        this period twice; this is run #3" instead of silently
        re-emitting an identical file.
        """
        cid = client_id if isinstance(client_id, uuid.UUID) else uuid.UUID(str(client_id))
        stmt = (
            self._scoped_select(TallyExportRun)
            .where(TallyExportRun.client_id == cid)
            .where(TallyExportRun.filing_month == filing_month)
            .where(TallyExportRun.filing_year == filing_year)
            .order_by(TallyExportRun.exported_at.desc())
        )
        return await self._fetch_all(stmt)


__all__ = ["TallyExportQuery"]
