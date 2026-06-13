"""Identity resolution — the 5-stage cascade.

When a document lands at ``/worker/document``, we know the firm (from
the chat_id → ca_firm mapping) but we don't yet know *which client* the
document belongs to. The cascade walks five stages in order and stops
at the first that produces an answer:

    1. **Session context** — the most recent conversation turn that
       carried a ``client_id`` (within the configured look-back window)
       wins. Cheap, high-precision when the CA has been actively
       working on one client.

    2. **Caption** — fuzzy match the Telegram caption text against
       ``clients.trade_name`` and ``clients.legal_name`` using
       ``pg_trgm`` similarity. Threshold is conservative; only HIGH
       wins outright.

    3. **GSTIN** — scan the caption AND the extracted text (if
       available) for a 15-char GSTIN. Exact-match a client's GSTIN →
       deterministic HIGH-confidence resolution.

    4. **Vendor history** — if the document was extracted and carries a
       vendor GSTIN, look up the (vendor_gstin → client) pairing that
       has appeared most often in this firm's documents. ADR-006: a
       vendor that's appeared ≥ 3 times against the same client gets
       MEDIUM confidence.

    5. **Explicit ask** — queue a ``pending_assignments`` row and signal
       the caller to send the CA a "which client?" inline-button
       prompt. No client is returned.

Every cascade run writes one row to ``identity_resolution_log`` with
``stage_reached`` and ``resolution_time_ms``.

The resolver is *pure routing*: it does not touch documents, run the
pipeline, or send Telegram messages. It returns a ``ResolutionOutcome``
that the caller acts on. This keeps the routing logic auditable in one
place — every cross-tenant test only needs to exercise this function.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import IntEnum, StrEnum
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.client import Client
from app.db.models.conversation import Conversation
from app.db.models.document import Document
from app.db.models.infrastructure import IdentityResolutionLog
from app.db.queries.clients import ClientQuery
from app.db.queries.pending_assignments import PendingAssignmentQuery
from app.logging_setup import get_logger
from app.tax.gstin_validator import is_valid_gstin

__all__ = [
    "CAPTION_TRGM_THRESHOLD",
    "SESSION_LOOKBACK_MINUTES",
    "VENDOR_HISTORY_MIN_HITS",
    "ResolutionConfidence",
    "ResolutionOutcome",
    "ResolutionStage",
    "resolve_identity",
]

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Tuning constants — surfaced as module-level so tests can patch them.
# ---------------------------------------------------------------------------

SESSION_LOOKBACK_MINUTES: Final[int] = 30
"""Window for stage 1: a conversation turn older than this is ignored."""

CAPTION_TRGM_THRESHOLD: Final[float] = 0.55
"""Minimum pg_trgm similarity for a caption to match a client."""

CAPTION_TRGM_HIGH_THRESHOLD: Final[float] = 0.80
"""Above this similarity stage 2 reports HIGH confidence."""

VENDOR_HISTORY_MIN_HITS: Final[int] = 3
"""Stage 4: a vendor must have been used by the same client at least
this many times before it wins."""

BUYER_NAME_TRGM_THRESHOLD: Final[float] = 0.55
"""R2 — minimum pg_trgm similarity between extracted buyer name and
``clients.trade_name``/``legal_name`` to consider it a routing
candidate."""

BUYER_NAME_TRGM_HIGH_THRESHOLD: Final[float] = 0.85
"""R2 — at or above this similarity the buyer-name match is treated as
HIGH confidence and the cascade returns immediately. Below it (down to
:data:`BUYER_NAME_TRGM_THRESHOLD`) the match is MEDIUM and we still
check the remaining stages for a better signal."""

# RFC 7159 (and the GSTIN spec) constrain the format to a 15-char block.
# We allow surrounding whitespace / punctuation in user-supplied text.
_GSTIN_FINDER: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Z0-9])([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z][A-Z][0-9A-Z])(?![A-Z0-9])"
)


# ---------------------------------------------------------------------------
# Outcome types
# ---------------------------------------------------------------------------


class ResolutionStage(IntEnum):
    """Which cascade stage produced the outcome.

    The integer value is what gets written to
    ``identity_resolution_log.stage_reached``. R2 added two new
    high-priority stages (``BUYER_GSTIN`` and ``BUYER_NAME``) with
    integer values 6 and 7 so existing analytics that count by stage_id
    don't have their meaning shift under them.
    """

    SESSION = 1
    CAPTION = 2
    GSTIN = 3
    VENDOR = 4
    EXPLICIT_ASK = 5
    BUYER_GSTIN = 6
    BUYER_NAME = 7


class ResolutionConfidence(StrEnum):
    """Confidence label written to ``identity_resolution_log.confidence``."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    EXPLICIT = "EXPLICIT"


@dataclass(frozen=True)
class ResolutionOutcome:
    """The result of a single cascade run.

    Exactly one of ``client_id`` (stages 1-4) or ``pending_assignment_id``
    (stage 5) will be populated.
    """

    stage: ResolutionStage
    confidence: ResolutionConfidence | None
    client_id: uuid.UUID | None
    pending_assignment_id: uuid.UUID | None
    resolution_time_ms: int
    log_id: uuid.UUID | None

    @property
    def is_resolved(self) -> bool:
        """True iff the cascade picked a deterministic client (stages 1-4)."""
        return self.client_id is not None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def resolve_identity(  # noqa: PLR0912 — linear cascade, splitting hurts readability
    *,
    session: AsyncSession,
    ca_firm_id: uuid.UUID,
    chat_id: int,
    caption: str | None,
    extracted_text: str | None,
    extracted_vendor_gstin: str | None,
    file_ids: list[str],
    message_id: int | None = None,
    now: datetime | None = None,
    extraction: dict[str, Any] | None = None,
    extraction_document_type: str | None = None,
) -> ResolutionOutcome:
    """Run the cascade and return the outcome.

    R2 inverted the cascade: the buyer fields from ``extract_only`` are
    now the first and strongest signals. The remaining stages
    (session/caption/GSTIN/vendor history) stay as fallbacks for
    uploads where the extractor produced nothing useful.

    Parameters
    ----------
    caption
        The Telegram message caption, if any (already trimmed by the
        gateway). May be ``None`` for messages that have no caption.
    extracted_text
        Optional pre-extracted text from the document (lets the resolver
        scan for GSTINs without re-OCR'ing).
    extracted_vendor_gstin
        The vendor GSTIN from the structured extraction. Used only by
        the vendor-history fallback.
    extraction
        R2 — the full extractor JSON (``vendor``, ``buyer``,
        ``totals``…) as produced by :func:`app.agents.pipeline.extract_only`.
        Stages 1 (BUYER_GSTIN) and 2 (BUYER_NAME) consume this. When
        ``None`` the resolver falls straight to the legacy stages.
    extraction_document_type
        R2 — classifier output. Cached on the pending_assignments row
        if EXPLICIT_ASK fires, so a future ``/assign`` can finalise
        without re-OCR.
    file_ids
        Telegram file_ids — stashed on the pending row if EXPLICIT_ASK
        fires.
    now
        Override clock for tests; defaults to ``datetime.now(UTC)``.
    """
    started = time.perf_counter()
    clock = now or datetime.now(UTC)

    buyer = (extraction or {}).get("buyer") if isinstance(extraction, dict) else None
    buyer_gstin = (
        str(buyer["gstin"]).strip().upper()
        if isinstance(buyer, dict) and isinstance(buyer.get("gstin"), str) and buyer["gstin"].strip()
        else None
    )
    buyer_name = (
        str(buyer["name"]).strip()
        if isinstance(buyer, dict) and isinstance(buyer.get("name"), str) and buyer["name"].strip()
        else None
    )

    # ---- R2 Stage 1: BUYER GSTIN (deterministic, HIGH) -------------------
    if buyer_gstin is not None and is_valid_gstin(buyer_gstin):
        client_q = ClientQuery(session=session, ca_firm_id=ca_firm_id)
        hit = await client_q.get_by_gstin(buyer_gstin)
        if hit is not None and hit.is_active:
            return await _emit(
                session=session,
                ca_firm_id=ca_firm_id,
                stage=ResolutionStage.BUYER_GSTIN,
                confidence=ResolutionConfidence.HIGH,
                client_id=hit.id,
                pending_id=None,
                started=started,
            )

    # ---- R2 Stage 2: BUYER NAME (fuzzy, HIGH ≥ 0.85 or MEDIUM ≥ 0.55) ----
    if buyer_name is not None:
        buyer_match = await _resolve_from_buyer_name(
            session=session, ca_firm_id=ca_firm_id, buyer_name=buyer_name
        )
        if buyer_match is not None:
            client_id, confidence = buyer_match
            if confidence is ResolutionConfidence.HIGH:
                return await _emit(
                    session=session,
                    ca_firm_id=ca_firm_id,
                    stage=ResolutionStage.BUYER_NAME,
                    confidence=confidence,
                    client_id=client_id,
                    pending_id=None,
                    started=started,
                )
            # MEDIUM: hold this candidate but keep checking the fallback
            # stages — a deterministic session/GSTIN signal still wins.

    # ---- Stage 3 (legacy): session context -------------------------------
    client_id = await _resolve_from_session(
        session=session,
        ca_firm_id=ca_firm_id,
        chat_id=chat_id,
        now=clock,
    )
    if client_id is not None:
        return await _emit(
            session=session,
            ca_firm_id=ca_firm_id,
            stage=ResolutionStage.SESSION,
            confidence=ResolutionConfidence.HIGH,
            client_id=client_id,
            pending_id=None,
            started=started,
        )

    # ---- Stage 4 (legacy): caption fuzzy match ---------------------------
    if caption:
        caption_match = await _resolve_from_caption(
            session=session, ca_firm_id=ca_firm_id, caption=caption
        )
        if caption_match is not None:
            client_id, confidence = caption_match
            return await _emit(
                session=session,
                ca_firm_id=ca_firm_id,
                stage=ResolutionStage.CAPTION,
                confidence=confidence,
                client_id=client_id,
                pending_id=None,
                started=started,
            )

    # ---- Stage 5 (legacy): GSTIN scan over caption+text ------------------
    gstin_match = await _resolve_from_gstin(
        session=session,
        ca_firm_id=ca_firm_id,
        haystacks=(caption, extracted_text),
    )
    if gstin_match is not None:
        return await _emit(
            session=session,
            ca_firm_id=ca_firm_id,
            stage=ResolutionStage.GSTIN,
            confidence=ResolutionConfidence.HIGH,
            client_id=gstin_match,
            pending_id=None,
            started=started,
        )

    # ---- Stage 6 (legacy): vendor history --------------------------------
    if extracted_vendor_gstin:
        vendor_match = await _resolve_from_vendor_history(
            session=session,
            ca_firm_id=ca_firm_id,
            vendor_gstin=extracted_vendor_gstin.upper(),
        )
        if vendor_match is not None:
            return await _emit(
                session=session,
                ca_firm_id=ca_firm_id,
                stage=ResolutionStage.VENDOR,
                confidence=ResolutionConfidence.MEDIUM,
                client_id=vendor_match,
                pending_id=None,
                started=started,
            )

    # ---- Stage 7: EXPLICIT_ASK (now informed by the extractor) -----------
    pending_id = await _queue_pending(
        session=session,
        ca_firm_id=ca_firm_id,
        chat_id=chat_id,
        message_id=message_id,
        file_ids=file_ids,
        extraction=extraction,
        extraction_document_type=extraction_document_type,
    )
    return await _emit(
        session=session,
        ca_firm_id=ca_firm_id,
        stage=ResolutionStage.EXPLICIT_ASK,
        confidence=ResolutionConfidence.EXPLICIT,
        client_id=None,
        pending_id=pending_id,
        started=started,
    )


# ---------------------------------------------------------------------------
# Stage 1: session context
# ---------------------------------------------------------------------------


async def _resolve_from_session(
    *,
    session: AsyncSession,
    ca_firm_id: uuid.UUID,
    chat_id: int,
    now: datetime,
) -> uuid.UUID | None:
    """Most recent conversation turn (within the look-back) wins."""
    cutoff = now - timedelta(minutes=SESSION_LOOKBACK_MINUTES)
    stmt = (
        select(Conversation.client_id)
        .where(Conversation.ca_firm_id == ca_firm_id)
        .where(Conversation.chat_id == chat_id)
        .where(Conversation.client_id.is_not(None))
        .where(Conversation.created_at >= cutoff)
        .order_by(Conversation.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)  # audit:allow-direct-session — ca_firm_id filtered above
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Stage 2: caption fuzzy match
# ---------------------------------------------------------------------------


async def _resolve_from_caption(
    *,
    session: AsyncSession,
    ca_firm_id: uuid.UUID,
    caption: str,
) -> tuple[uuid.UUID, ResolutionConfidence] | None:
    """pg_trgm similarity over trade_name and legal_name.

    Returns the best candidate iff:
      * its similarity ≥ ``CAPTION_TRGM_THRESHOLD``
      * AND its margin over the runner-up is ≥ 0.05 (no tie ambiguity)
    """
    caption_trim = caption.strip()
    if len(caption_trim) < 3:
        return None

    sim_trade = func.similarity(Client.trade_name, caption_trim)
    sim_legal = func.similarity(func.coalesce(Client.legal_name, ""), caption_trim)
    similarity = func.greatest(sim_trade, sim_legal).label("similarity")

    stmt = (
        select(Client.id, similarity)
        .where(Client.ca_firm_id == ca_firm_id)
        .where(Client.is_active.is_(True))
        .where(similarity >= CAPTION_TRGM_THRESHOLD)
        .order_by(similarity.desc())
        .limit(2)
    )
    result = await session.execute(stmt)  # audit:allow-direct-session — ca_firm_id filtered above
    rows = result.all()
    if not rows:
        return None

    top_id, top_sim = rows[0]
    if len(rows) >= 2:
        _, runner_sim = rows[1]
        # Reject if two candidates are too close together.
        if float(top_sim) - float(runner_sim) < 0.05:
            return None

    confidence = (
        ResolutionConfidence.HIGH
        if float(top_sim) >= CAPTION_TRGM_HIGH_THRESHOLD
        else ResolutionConfidence.MEDIUM
    )
    return uuid.UUID(str(top_id)), confidence


# ---------------------------------------------------------------------------
# Stage 3: GSTIN scan
# ---------------------------------------------------------------------------


async def _resolve_from_gstin(
    *,
    session: AsyncSession,
    ca_firm_id: uuid.UUID,
    haystacks: tuple[str | None, ...],
) -> uuid.UUID | None:
    """Scan provided text blobs for a valid GSTIN that matches a client."""
    candidates: list[str] = []
    for blob in haystacks:
        if not blob:
            continue
        for match in _GSTIN_FINDER.finditer(blob.upper()):
            gstin = match.group(1)
            if is_valid_gstin(gstin) and gstin not in candidates:
                candidates.append(gstin)

    if not candidates:
        return None

    client_q = ClientQuery(session=session, ca_firm_id=ca_firm_id)
    for gstin in candidates:
        hit = await client_q.get_by_gstin(gstin)
        if hit is not None and hit.is_active:
            return hit.id
    return None


# ---------------------------------------------------------------------------
# Stage 4: vendor history
# ---------------------------------------------------------------------------


async def _resolve_from_vendor_history(
    *,
    session: AsyncSession,
    ca_firm_id: uuid.UUID,
    vendor_gstin: str,
) -> uuid.UUID | None:
    """Pick the client this vendor's GSTIN has been seen with most often.

    Requires ≥ ``VENDOR_HISTORY_MIN_HITS`` matching documents. The
    runner-up must be at least 2 hits behind (otherwise we treat the
    history as ambiguous and fall through to stage 5).
    """
    hit_count = func.count(Document.id).label("hits")
    vendor_path = Document.extraction_data["vendor"]["gstin"].astext

    stmt = (
        select(Document.client_id, hit_count)
        .where(Document.ca_firm_id == ca_firm_id)
        .where(vendor_path == vendor_gstin)
        .group_by(Document.client_id)
        .order_by(hit_count.desc())
        .limit(2)
    )
    result = await session.execute(stmt)  # audit:allow-direct-session — ca_firm_id filtered above
    rows = result.all()
    if not rows:
        return None

    top_client, top_hits = rows[0]
    if int(top_hits) < VENDOR_HISTORY_MIN_HITS:
        return None
    if len(rows) >= 2:
        _, runner_hits = rows[1]
        if int(top_hits) - int(runner_hits) < 2:
            return None
    return uuid.UUID(str(top_client))


# ---------------------------------------------------------------------------
# Stage 5: explicit ask
# ---------------------------------------------------------------------------


async def _resolve_from_buyer_name(
    *,
    session: AsyncSession,
    ca_firm_id: uuid.UUID,
    buyer_name: str,
) -> tuple[uuid.UUID, ResolutionConfidence] | None:
    """R2 — pg_trgm similarity between extracted buyer name and clients.

    Mirrors :func:`_resolve_from_caption` but tuned for extractor output:

    * Higher HIGH threshold (0.85 vs 0.80 for captions) — extractor text
      is cleaner and we don't want a stray "Foo Corp" upload to land on
      "FooCorp Pvt Ltd" without an explicit confirm.
    * Returns MEDIUM down to :data:`BUYER_NAME_TRGM_THRESHOLD` so the
      caller can still consider the candidate after running the other
      fallback stages.
    """
    name = buyer_name.strip()
    if len(name) < 3:
        return None

    sim_trade = func.similarity(Client.trade_name, name)
    sim_legal = func.similarity(func.coalesce(Client.legal_name, ""), name)
    similarity = func.greatest(sim_trade, sim_legal).label("similarity")

    stmt = (
        select(Client.id, similarity)
        .where(Client.ca_firm_id == ca_firm_id)
        .where(Client.is_active.is_(True))
        .where(similarity >= BUYER_NAME_TRGM_THRESHOLD)
        .order_by(similarity.desc())
        .limit(2)
    )
    result = await session.execute(stmt)  # audit:allow-direct-session — ca_firm_id filtered above
    rows = result.all()
    if not rows:
        return None

    top_id, top_sim = rows[0]
    if len(rows) >= 2:
        _, runner_sim = rows[1]
        # Tie-break: if two clients are within 0.05 similarity of each other
        # the routing is ambiguous regardless of HIGH/MEDIUM — bail.
        if float(top_sim) - float(runner_sim) < 0.05:
            return None

    confidence = (
        ResolutionConfidence.HIGH
        if float(top_sim) >= BUYER_NAME_TRGM_HIGH_THRESHOLD
        else ResolutionConfidence.MEDIUM
    )
    return uuid.UUID(str(top_id)), confidence


async def _queue_pending(
    *,
    session: AsyncSession,
    ca_firm_id: uuid.UUID,
    chat_id: int,
    message_id: int | None,
    file_ids: list[str],
    extraction: dict[str, Any] | None = None,
    extraction_document_type: str | None = None,
) -> uuid.UUID:
    """Insert a pending_assignments row and return its id.

    R2 — ``extraction`` + ``extraction_document_type`` are cached so the
    eventual ``/assign`` or inline-button confirmation can call
    :func:`app.agents.pipeline.finalize_document` without re-OCR'ing.
    """
    q = PendingAssignmentQuery(session=session, ca_firm_id=ca_firm_id)
    row = await q.create(
        chat_id=chat_id,
        file_ids=file_ids,
        message_id=message_id,
        extraction=extraction,
        extraction_document_type=extraction_document_type,
    )
    return row.id


# ---------------------------------------------------------------------------
# Audit log helper
# ---------------------------------------------------------------------------


async def _emit(
    *,
    session: AsyncSession,
    ca_firm_id: uuid.UUID,
    stage: ResolutionStage,
    confidence: ResolutionConfidence | None,
    client_id: uuid.UUID | None,
    pending_id: uuid.UUID | None,
    started: float,
) -> ResolutionOutcome:
    """Write the audit-log row and return the outcome."""
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    audit = IdentityResolutionLog(
        ca_firm_id=ca_firm_id,
        document_id=None,
        stage_reached=int(stage),
        matched_client_id=client_id,
        confidence=confidence.value if confidence is not None else None,
        resolution_time_ms=elapsed_ms,
    )
    session.add(audit)  # audit:allow-direct-session — audit log uses ca_firm_id from outer scope
    await session.flush()
    _log.info(
        "identity_resolved",
        ca_firm_id=str(ca_firm_id),
        stage=stage.name,
        confidence=confidence.value if confidence else None,
        client_id=str(client_id) if client_id else None,
        pending_assignment_id=str(pending_id) if pending_id else None,
        resolution_time_ms=elapsed_ms,
    )
    return ResolutionOutcome(
        stage=stage,
        confidence=confidence,
        client_id=client_id,
        pending_assignment_id=pending_id,
        resolution_time_ms=elapsed_ms,
        log_id=audit.id,
    )
