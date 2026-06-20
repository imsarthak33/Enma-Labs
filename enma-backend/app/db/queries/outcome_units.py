"""Outcome-unit CRUD — billable artefacts, firm-scoped.

Outcomes are the unit of pricing in Track A (per filed return, per
recovered ITC rupee) and Track B (same units, internal billing). One
write path (``record``) and a small set of reads — the TA-1 outcome
meter query ``sum_by_kind`` is the most-used downstream.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import func, select

from app.db.models.outcome_unit import OutcomeUnit
from app.db.queries.base import BaseQuery


class OutcomeUnitQuery(BaseQuery):
    """Tenant-scoped outcome operations."""

    async def record(
        self,
        *,
        kind: str,
        quantity: Decimal,
        client_id: uuid.UUID | str | None = None,
        confidence: Decimal | None = None,
        ca_approval_status: str = "pending",
        related_document_id: uuid.UUID | str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OutcomeUnit:
        """Insert one outcome row. ``quantity`` is always a :class:`Decimal`."""
        cid = (
            client_id
            if client_id is None or isinstance(client_id, uuid.UUID)
            else uuid.UUID(str(client_id))
        )
        did = (
            related_document_id
            if related_document_id is None
            or isinstance(related_document_id, uuid.UUID)
            else uuid.UUID(str(related_document_id))
        )
        row = OutcomeUnit(
            client_id=cid,
            kind=kind,
            quantity=quantity,
            confidence=confidence,
            ca_approval_status=ca_approval_status,
            related_document_id=did,
            metadata_=metadata or {},
        )
        return cast(OutcomeUnit, await self._insert(row))

    async def sum_by_kind(
        self,
        *,
        kind: str,
        client_id: uuid.UUID | str | None = None,
    ) -> Decimal:
        """Total ``quantity`` for one ``kind`` across this firm.

        Used by the TA-1 outcome meter. Returns ``Decimal('0')`` when
        nothing matches — never ``None``.
        """
        stmt = (
            select(func.coalesce(func.sum(OutcomeUnit.quantity), 0))
            .where(OutcomeUnit.ca_firm_id == self.ca_firm_id)
            .where(OutcomeUnit.kind == kind)
        )
        if client_id is not None:
            cid = (
                client_id
                if isinstance(client_id, uuid.UUID)
                else uuid.UUID(str(client_id))
            )
            stmt = stmt.where(OutcomeUnit.client_id == cid)
        result = await self.session.execute(stmt)
        value = result.scalar_one()
        return Decimal(str(value))

    async def list_recent(self, limit: int = 25) -> Sequence[OutcomeUnit]:
        """Most-recent outcomes for this firm."""
        stmt = (
            self._scoped_select(OutcomeUnit)
            .order_by(OutcomeUnit.created_at.desc())
            .limit(limit)
        )
        return await self._fetch_all(stmt)


__all__ = ["OutcomeUnitQuery"]
