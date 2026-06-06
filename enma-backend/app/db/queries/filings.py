"""Filing-approval lookups + writes — locked-period awareness.

The tax engine needs to know which ``(month, year)`` periods this firm
has already approved so it can:

  * Refuse to recompute verdicts for documents inside a locked period
    (the frozen-verdict invariant from ADR-005 rev 2 revision #4).
  * Apply the period_only defer strategy: a cross-period invoice that
    sits inside a *locked* period defers; one in an *open* period
    claims now.

Writes happen exactly once per ``(firm, month, year)`` via
:meth:`FilingQuery.create_approval`, called from the Phase 6 filing
approval flow. The ``UNIQUE(ca_firm_id, filing_month, filing_year)``
constraint makes the operation idempotent — a second attempt raises
:class:`FilingAlreadyApproved`.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy.exc import IntegrityError

from app.db.models.filing import FilingApproval
from app.db.queries.base import BaseQuery

__all__ = [
    "FilingAlreadyApproved",
    "FilingQuery",
]


class FilingAlreadyApproved(Exception):
    """Raised when a (firm, month, year) approval already exists."""

    def __init__(self, month: int, year: int) -> None:
        super().__init__(f"filing already approved for {month}/{year}")
        self.month = month
        self.year = year


class FilingQuery(BaseQuery):
    """Tenant-scoped filing-approval reads + writes."""

    # ----- reads -----------------------------------------------------------

    async def list_locked_periods(self) -> frozenset[tuple[int, int]]:
        """Return the set of ``(month, year)`` periods already approved.

        Used by the pipeline to assemble ``PeriodContext.locked_periods``
        before invoking the tax engine.
        """
        stmt = self._scoped_select(FilingApproval)
        rows = await self._fetch_all(stmt)
        return frozenset((row.filing_month, row.filing_year) for row in rows)

    async def is_period_locked(self, *, month: int, year: int) -> bool:
        """Cheap single-period lookup used by the document writer."""
        stmt = (
            self._scoped_select(FilingApproval)
            .where(FilingApproval.filing_month == month)
            .where(FilingApproval.filing_year == year)
            .limit(1)
        )
        return (await self._fetch_one(stmt)) is not None

    async def get_approval(
        self, *, month: int, year: int
    ) -> FilingApproval | None:
        """Return the existing approval row for ``(month, year)``, if any."""
        stmt = (
            self._scoped_select(FilingApproval)
            .where(FilingApproval.filing_month == month)
            .where(FilingApproval.filing_year == year)
            .limit(1)
        )
        return await self._fetch_one(stmt)

    # ----- writes ----------------------------------------------------------

    async def create_approval(
        self,
        *,
        month: int,
        year: int,
        approved_by_chat_id: int,
        filing_snapshot: dict[str, Any],
        approval_hash: str,
    ) -> FilingApproval:
        """Insert a new filing approval. Idempotent via DB unique key.

        Raises :class:`FilingAlreadyApproved` if a row for this
        ``(firm, month, year)`` already exists — call
        :meth:`is_period_locked` first if you want to check without
        relying on the exception path.
        """
        row = FilingApproval(
            filing_month=month,
            filing_year=year,
            approved_by_chat_id=approved_by_chat_id,
            filing_snapshot=filing_snapshot,
            approval_hash=approval_hash,
        )
        try:
            return cast(FilingApproval, await self._insert(row))
        except IntegrityError as exc:
            await self.session.rollback()
            raise FilingAlreadyApproved(month=month, year=year) from exc
