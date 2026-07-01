"""Reconciliation-run CRUD — immutable audit, firm-scoped (ADR-015)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import cast

from app.db.models.reconciliation_run import ReconciliationRun
from app.db.queries.base import BaseQuery


class ReconRunQuery(BaseQuery):
    """Tenant-scoped reconciliation-run operations."""

    async def record(
        self,
        *,
        client_id: uuid.UUID,
        filing_period_month: int,
        filing_period_year: int,
        matched_count: int,
        amount_mismatch_count: int,
        in_books_not_in_2b_count: int,
        in_2b_not_in_books_count: int,
        recoverable_itc: Decimal,
        at_risk_itc: Decimal,
        report_sha256: str | None = None,
        run_by_chat_id: int | None = None,
        kind: str = "invoice_vs_2b",
    ) -> ReconciliationRun:
        """Append one immutable reconciliation-run audit row."""
        row = ReconciliationRun(
            client_id=client_id,
            filing_period_month=filing_period_month,
            filing_period_year=filing_period_year,
            kind=kind,
            matched_count=matched_count,
            amount_mismatch_count=amount_mismatch_count,
            in_books_not_in_2b_count=in_books_not_in_2b_count,
            in_2b_not_in_books_count=in_2b_not_in_books_count,
            recoverable_itc=recoverable_itc,
            at_risk_itc=at_risk_itc,
            report_sha256=report_sha256,
            run_by_chat_id=run_by_chat_id,
        )
        return cast(ReconciliationRun, await self._insert(row))

    async def exists_for_period(
        self,
        *,
        client_id: uuid.UUID,
        filing_period_month: int,
        filing_period_year: int,
        kind: str | None = None,
    ) -> bool:
        """True iff a recon run already exists for this client + period.

        The Phase 8d period-close assembler uses this (with
        ``kind='period_close_report'``) to deliver the complete-legs report
        at most once per period, no matter how often the cron re-fires.
        """
        stmt = (
            self._scoped_select(ReconciliationRun)
            .where(ReconciliationRun.client_id == client_id)
            .where(ReconciliationRun.filing_period_month == filing_period_month)
            .where(ReconciliationRun.filing_period_year == filing_period_year)
        )
        if kind is not None:
            stmt = stmt.where(ReconciliationRun.kind == kind)
        stmt = stmt.limit(1)
        return await self._fetch_one(stmt) is not None

    async def list_recent(
        self, *, limit: int = 25, client_id: uuid.UUID | None = None
    ) -> Sequence[ReconciliationRun]:
        """Most-recent recon runs for this firm, optionally one client only."""
        stmt = self._scoped_select(ReconciliationRun)
        if client_id is not None:
            stmt = stmt.where(ReconciliationRun.client_id == client_id)
        stmt = stmt.order_by(ReconciliationRun.created_at.desc()).limit(limit)
        return await self._fetch_all(stmt)


__all__ = ["ReconRunQuery"]
