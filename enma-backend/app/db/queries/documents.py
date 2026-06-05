"""Document CRUD operations — all scoped to ca_firm_id."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, cast

from app.db.models.document import Document
from app.db.queries.base import BaseQuery


class DocumentQuery(BaseQuery):
    """Tenant-scoped document operations."""

    async def list_all(self) -> Sequence[Document]:
        """Return all documents for this firm."""
        stmt = self._scoped_select(Document).order_by(Document.created_at.desc())
        return await self._fetch_all(stmt)

    async def list_by_client(
        self, client_id: uuid.UUID | str
    ) -> Sequence[Document]:
        """Return all documents for a specific client (firm-scoped)."""
        stmt = self._scoped_select_client(Document, client_id).order_by(
            Document.created_at.desc()
        )
        return await self._fetch_all(stmt)

    async def get_by_id(self, document_id: uuid.UUID | str) -> Document | None:
        """Fetch a single document by ID (firm-scoped)."""
        did = (
            document_id
            if isinstance(document_id, uuid.UUID)
            else uuid.UUID(str(document_id))
        )
        stmt = self._scoped_select(Document).where(Document.id == did)
        return await self._fetch_one(stmt)

    async def list_by_filing_period(
        self, year: int, month: int
    ) -> Sequence[Document]:
        """Return all documents for a filing period (firm-scoped)."""
        stmt = (
            self._scoped_select(Document)
            .where(Document.filing_period_year == year)
            .where(Document.filing_period_month == month)
            .order_by(Document.created_at.desc())
        )
        return await self._fetch_all(stmt)

    async def create(
        self,
        *,
        client_id: uuid.UUID | str,
        document_type: str,
        source_file_ids: dict[str, Any],
        extraction_data: dict[str, Any],
        tax_verdict: dict[str, Any] | None = None,
        verification_result: dict[str, Any] | None = None,
        filing_period_month: int | None = None,
        filing_period_year: int | None = None,
    ) -> Document:
        """Insert a new document for this firm."""
        cid = (
            client_id
            if isinstance(client_id, uuid.UUID)
            else uuid.UUID(str(client_id))
        )
        doc = Document(
            client_id=cid,
            document_type=document_type,
            source_file_ids=source_file_ids,
            extraction_data=extraction_data,
            tax_verdict=tax_verdict,
            verification_result=verification_result,
            filing_period_month=filing_period_month,
            filing_period_year=filing_period_year,
        )
        return cast(Document, await self._insert(doc))

    async def update_verdict(
        self,
        document_id: uuid.UUID | str,
        *,
        tax_verdict: dict[str, Any],
        verification_result: dict[str, Any] | None = None,
        processing_status: str = "completed",
        processing_time_ms: int | None = None,
    ) -> Document | None:
        """Update the tax verdict and processing status."""
        return await self._update(
            Document,
            document_id,
            tax_verdict=tax_verdict,
            verification_result=verification_result,
            processing_status=processing_status,
            processing_time_ms=processing_time_ms,
        )


__all__ = ["DocumentQuery"]
