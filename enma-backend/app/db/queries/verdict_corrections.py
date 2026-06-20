"""Verdict-correction CRUD — the LoRA training corpus.

Single write path (``record``). The caller is responsible for
anonymising ``invoice_features_anonymized`` BEFORE invoking; the
table never stores un-anonymized payload (single-stage write).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, cast

from app.db.models.verdict_correction import VerdictCorrection
from app.db.queries.base import BaseQuery


class VerdictCorrectionQuery(BaseQuery):
    """Tenant-scoped verdict-correction operations."""

    async def record(
        self,
        *,
        source_document_id: uuid.UUID | str | None,
        original_verdict: dict[str, Any],
        corrected_verdict: dict[str, Any],
        invoice_features_anonymized: dict[str, Any],
        correction_reason_text: str | None = None,
        corrected_by_chat_id: int | None = None,
        track: str = "A",
        confidence_self_assessed: Decimal | None = None,
    ) -> VerdictCorrection:
        """Append one CA correction to the corpus."""
        did = (
            source_document_id
            if source_document_id is None
            or isinstance(source_document_id, uuid.UUID)
            else uuid.UUID(str(source_document_id))
        )
        row = VerdictCorrection(
            source_document_id=did,
            original_verdict=original_verdict,
            corrected_verdict=corrected_verdict,
            correction_reason_text=correction_reason_text,
            corrected_by_chat_id=corrected_by_chat_id,
            track=track,
            confidence_self_assessed=confidence_self_assessed,
            invoice_features_anonymized=invoice_features_anonymized,
        )
        return cast(VerdictCorrection, await self._insert(row))

    async def list_for_document(
        self, *, document_id: uuid.UUID | str
    ) -> Sequence[VerdictCorrection]:
        """All corrections recorded for one document (firm-scoped)."""
        did = (
            document_id
            if isinstance(document_id, uuid.UUID)
            else uuid.UUID(str(document_id))
        )
        stmt = (
            self._scoped_select(VerdictCorrection)
            .where(VerdictCorrection.source_document_id == did)
            .order_by(VerdictCorrection.created_at.desc())
        )
        return await self._fetch_all(stmt)


__all__ = ["VerdictCorrectionQuery"]
