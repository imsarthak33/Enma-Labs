"""Document CRUD operations — all scoped to ca_firm_id."""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import String
from sqlalchemy import cast as sa_cast

from app.db.models.document import Document
from app.db.queries.base import BaseQuery

# 8-hex-char prefix is what the pipeline summary shows the user as
# "Ref:". The supervisor LLM picks that up and passes it into tools
# expecting a UUID, so we accept any 4-32 hex-char prefix here.
_REF_PREFIX_RE: re.Final = re.compile(r"^[0-9a-fA-F]{4,32}$")


class DocumentQuery(BaseQuery):
    """Tenant-scoped document operations."""

    async def list_all(self) -> Sequence[Document]:
        """Return all documents for this firm."""
        stmt = self._scoped_select(Document).order_by(Document.created_at.desc())
        return await self._fetch_all(stmt)

    async def list_by_client(self, client_id: uuid.UUID | str) -> Sequence[Document]:
        """Return all documents for a specific client (firm-scoped)."""
        stmt = self._scoped_select_client(Document, client_id).order_by(Document.created_at.desc())
        return await self._fetch_all(stmt)

    async def get_by_id(self, document_id: uuid.UUID | str) -> Document | None:
        """Fetch a single document by ID (firm-scoped)."""
        did = document_id if isinstance(document_id, uuid.UUID) else uuid.UUID(str(document_id))
        stmt = self._scoped_select(Document).where(Document.id == did)
        return await self._fetch_one(stmt)

    async def find_by_ref_prefix(self, ref: str) -> list[Document]:
        """Resolve an 8-hex 'Ref:' prefix back to the underlying document(s).

        The pipeline summary renders the document UUID truncated to its
        first eight hex chars as the user-facing "Ref:". CAs naturally
        quote that ref back ("invoice with ref 4b8d91e0"), and the
        supervisor LLM forwards it into tools that expect a full UUID.
        This helper takes the prefix and returns every document in this
        firm whose id starts with it.

        Empty list if no match. Multiple matches are possible (collisions
        are rare but valid for very short prefixes) — the caller decides
        what to do.

        Raises:
            ValueError: if ``ref`` is not 4-32 hex chars.
        """
        cleaned = ref.strip().lower()
        if not _REF_PREFIX_RE.fullmatch(cleaned):
            raise ValueError(
                f"ref must be 4-32 hexadecimal characters, got {ref!r}"
            )
        stmt = (
            self._scoped_select(Document)
            .where(sa_cast(Document.id, String).ilike(f"{cleaned}%"))
            .order_by(Document.created_at.desc())
            .limit(5)
        )
        return list(await self._fetch_all(stmt))

    async def list_by_filing_period(self, year: int, month: int) -> Sequence[Document]:
        """Return all documents for a filing period (firm-scoped)."""
        stmt = (
            self._scoped_select(Document)
            .where(Document.filing_period_year == year)
            .where(Document.filing_period_month == month)
            .order_by(Document.created_at.desc())
        )
        return await self._fetch_all(stmt)

    async def find_by_content_hash(
        self, *, client_id: uuid.UUID | str, content_hash: str
    ) -> Document | None:
        """W3-h7: lookup an existing document by its natural-key hash.

        Used by ``finalize_document`` to skip re-processing when the CA
        re-uploads the same real-world invoice (same vendor + invoice
        number + invoice date). Returns the existing row if any; the
        caller then short-circuits the persistence step.
        """
        if not content_hash:
            return None
        cid = client_id if isinstance(client_id, uuid.UUID) else uuid.UUID(str(client_id))
        stmt = (
            self._scoped_select(Document)
            .where(Document.client_id == cid)
            .where(Document.content_hash == content_hash)
            .order_by(Document.created_at.desc())
            .limit(1)
        )
        return await self._fetch_one(stmt)

    async def find_by_invoice_number(
        self,
        *,
        invoice_number: str,
        client_id: uuid.UUID | str | None = None,
    ) -> list[Document]:
        """Look up documents by the invoice number printed on the page.

        ``find_by_ref_prefix`` takes the short UUID hex shown on the
        pipeline summary, but CAs naturally quote the *human* invoice
        number ("share me invoice 91"). This helper searches the
        JSONB ``extraction_data->>'invoice_number'`` field. Optionally
        scopes to one client; otherwise returns matches across every
        client in this firm.
        """
        cleaned = invoice_number.strip()
        if not cleaned:
            return []
        stmt = self._scoped_select(Document).where(
            Document.extraction_data["invoice_number"].astext == cleaned
        )
        if client_id is not None:
            cid = client_id if isinstance(client_id, uuid.UUID) else uuid.UUID(str(client_id))
            stmt = stmt.where(Document.client_id == cid)
        stmt = stmt.order_by(Document.created_at.desc()).limit(10)
        return list(await self._fetch_all(stmt))

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
        processing_status: str | None = None,
        processing_time_ms: int | None = None,
        content_hash: str | None = None,
    ) -> Document:
        """Insert a new document for this firm.

        ``processing_status`` defaults to ``"pending"`` via the model's
        server-side default. Pipeline writers should pass ``"completed"``
        once verdict + verification have succeeded, otherwise the row
        sits in ``pending`` forever and silently disappears from every
        export and filing query that requires a terminal status.
        """
        cid = client_id if isinstance(client_id, uuid.UUID) else uuid.UUID(str(client_id))
        kwargs: dict[str, Any] = {
            "client_id": cid,
            "document_type": document_type,
            "source_file_ids": source_file_ids,
            "extraction_data": extraction_data,
            "tax_verdict": tax_verdict,
            "verification_result": verification_result,
            "filing_period_month": filing_period_month,
            "filing_period_year": filing_period_year,
        }
        if processing_status is not None:
            kwargs["processing_status"] = processing_status
        if processing_time_ms is not None:
            kwargs["processing_time_ms"] = processing_time_ms
        if content_hash is not None:
            kwargs["content_hash"] = content_hash
        doc = Document(**kwargs)
        return cast(Document, await self._insert(doc))

    async def update_status(
        self,
        document_id: uuid.UUID | str,
        *,
        processing_status: str,
    ) -> Document | None:
        """W2.D — flip processing_status for one document (firm-scoped).

        Used by the supervisor's ``mark_document`` tool when a CA says
        "mark doc 1ca3f4e0 as approved" / "flag doc abc as needs-review".
        Status strings are not validated here; the API surface accepts
        any short string the supervisor passes, but the document model
        itself constrains the column to a VARCHAR.
        """
        return await self._update(
            Document,
            document_id,
            processing_status=processing_status,
        )

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
