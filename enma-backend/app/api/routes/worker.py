"""THE single worker controller.

Five endpoints, one file. Every handler obeys the same sequence:

    1. verify_envelope             (Depends; 401 on failure)
    2. check_idempotency           (Depends; returns verdict)
    3. if duplicate → return 200 with no side-effects
    4. spawn the per-kind background pipeline (registry-managed)
    5. send the Telegram ACK ("<i>Processing...</i>") via services.telegram
    6. return 202 Accepted with the new idempotency log_id

Phase 4 implements the document pipeline: download → classify → extract →
verify → persist → HTML summary. Other kinds (command/voice/callback)
still use the Phase-3 logging stub until their phases land:

    document     → Phase 4 (full pipeline)
    document_batch → Phase 4 stub (loops single-doc handling in Phase 4.5)
    command      → Phase 6
    voice        → Phase 7
    callback     → Phase 6

Why a single file
-----------------
The architecture document explicitly bans multiple ``worker_*.py`` files.
A single point of authentication and idempotency is easier to audit, and
new envelope kinds slot in as additional handlers without polluting other
parts of the codebase.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import ORJSONResponse, Response

from app.agents.commands import (
    dispatch_command,
    is_slash_command,
    parse_command,
)
from app.agents.filing_approval import (
    APPROVAL_REGEX,
    FilingPeriodAlreadyLocked,
    InvalidApprovalString,
    finalize_approval,
    parse_approval_intent,
)
from app.agents.onboarding import (
    handle_onboarding_reply,
    handle_start,
    is_in_onboarding,
)
from app.agents.pipeline import (
    ExtractionOutcome,
    PipelineError,
    extract_only,
    finalize_document,
    run_pipeline,
)
from app.agents.supervisor import SupervisorContext, run_supervisor
from app.api.middleware.envelope_verify import DecodedEnvelope, VerifiedEnvelopeDep
from app.api.middleware.idempotency import IdempotencyDep, IdempotencyVerdict
from app.db.models.client import Client
from app.db.models.firm import CaFirm
from app.db.queries.brain_events import BrainEventQuery
from app.db.queries.clients import ClientQuery
from app.db.queries.conversations import RECENT_WINDOW_TURNS, ConversationQuery
from app.db.queries.documents import DocumentQuery
from app.db.queries.firms import find_firm_by_admin_chat_id
from app.db.queries.pending_assignments import PendingAssignmentQuery
from app.db.queries.trajectories import TrajectoryQuery
from app.db.session import get_sessionmaker
from app.formatting.pipeline_summary import render_pipeline_summary
from app.formatting.telegram_html import bold, code, italic, safe_text
from app.identity import resolve_identity
from app.identity.resolver import (
    BUYER_NAME_TRGM_HIGH_THRESHOLD,
    BUYER_NAME_TRGM_THRESHOLD,
)
from app.logging_setup import get_logger
from app.services import gstr2b_import, recon_runner, tally_import, telegram
from app.services.llm import LLMError
from app.services.messaging import factory as messaging_factory
from app.services.messaging.base import RawHtml
from app.utils.background import get_registry

router = APIRouter(prefix="/worker", tags=["worker"])

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Document pipeline (Phase 4)
# ---------------------------------------------------------------------------


async def _run_document_pipeline(envelope: DecodedEnvelope) -> None:  # noqa: PLR0911
    """Background task for a single document envelope (R2 — extract-first).

    The autonomous-routing flow:

      1. Resolve firm by ``chat_id``.
      2. Download the file from Telegram.
      3. Run ``extract_only`` — get the scout's view of the invoice
         (buyer/vendor GSTINs, names, totals, document type).
      4. Hand the extraction to ``resolve_identity`` which now routes
         on extracted buyer fields first.
      5a. If routed → call ``finalize_document`` and reply with summary.
      5b. If ambiguous → cache the extraction on the pending_assignments
          row (already done by the resolver) and send an informed
          "which client?" prompt that shows the extracted buyer details.

    Any handled failure ends with an HTML "couldn't process" message so
    the user sees feedback even on the unhappy path.
    """
    if envelope.chat_id is None:
        _log.warning("document_pipeline_no_chat_id", message_id=envelope.message_id)
        return

    file_id = _extract_file_id(envelope.payload)
    if file_id is None:
        await _send_user_error(envelope.chat_id, "Could not find a file in the message.")
        return

    caption = _extract_caption(envelope.payload)

    factory = get_sessionmaker()
    async with factory() as session:
        firm = await find_firm_by_admin_chat_id(session, envelope.chat_id)
        if firm is None:
            await _send_user_error(
                envelope.chat_id,
                "No firm is registered for this Telegram account. "
                "Please contact your administrator.",
            )
            return

        # ---- Step 2: download the file ----------------------------------
        try:
            file_bytes = await telegram.download_file(file_id)
        except telegram.TelegramAPIError as exc:
            _log.error("document_pipeline_download_failed", error=str(exc))
            await _send_user_error(
                envelope.chat_id,
                "I couldn't download that file from Telegram. Could you try again?",
            )
            return

        # ---- Step 2a: Tally export? (A2 — Brain ingestion) --------------
        # A Tally voucher XML is structured accounting data, not an invoice
        # to OCR. Sniff before the extractor runs and branch to the Brain
        # ingestion handler (ADR-014). Re-uploads are deduped at the
        # brain_events layer (content hash), so this bypasses the
        # document-level early-dedup intentionally.
        if tally_import.looks_like_tally_xml(file_bytes):
            await _run_tally_ingestion(
                session=session,
                firm=firm,
                chat_id=envelope.chat_id,
                reply_to_message_id=envelope.message_id,
                file_bytes=file_bytes,
                caption=caption,
            )
            return

        # ---- Step 2a': GSTR-2B JSON? (TA-2 — ingest + auto-reconcile) ---
        # A GSTR-2B export is the available-ITC leg, not an invoice. Ingest
        # it to brain_events and immediately reconcile against the client's
        # books for the return period (ADR-015).
        if gstr2b_import.looks_like_gstr2b_json(file_bytes):
            await _run_gstr2b_ingestion(
                session=session,
                firm=firm,
                chat_id=envelope.chat_id,
                reply_to_message_id=envelope.message_id,
                file_bytes=file_bytes,
            )
            return

        # ---- Step 2b: early dedup (W3.5-d) ------------------------------
        # SHA-256 the raw bytes and check the DB BEFORE we spend money on
        # the layout + extraction LLM calls. Hash matches mean the CA
        # uploaded the same file before; render a dedup banner and stop.
        source_file_hash = hashlib.sha256(file_bytes).hexdigest()
        docs_q = DocumentQuery(session=session, ca_firm_id=firm.id)
        existing_dup = await docs_q.find_by_source_file_hash(
            source_file_hash=source_file_hash
        )
        if existing_dup is not None:
            _log.info(
                "document_early_dedup_hit",
                existing_id=str(existing_dup.id),
                source_file_hash=source_file_hash,
            )
            dedup_html = (
                f"<i>Re-upload detected.</i> Already in your ledger as "
                f"<code>{str(existing_dup.id)[:8]}</code> — nothing new "
                f"processed. (Saved one extraction.)"
            )
            # W4-P2c — route via the messaging factory so a WhatsApp firm
            # gets the dedup banner over WA when the time comes. Telegram
            # firms keep the same wire as pre-W4 via TelegramClient.
            await messaging_factory.client_for(firm=firm).send_message(
                recipient=envelope.chat_id,
                body=RawHtml(dedup_html),
                reply_to_id=envelope.message_id,
            )
            # W3.5-e — persist this turn too, so the LLM knows "the
            # user re-uploaded the file I already have as <ref>".
            try:
                await ConversationQuery(session=session, ca_firm_id=firm.id).append_turn(
                    chat_id=envelope.chat_id,
                    role="assistant",
                    content=dedup_html,
                    client_id=existing_dup.client_id,
                    metadata={
                        "kind": "document_dedup",
                        "document_id": str(existing_dup.id),
                    },
                )
                await session.commit()
            except Exception as exc:  # — memory must not break user reply
                _log.error("conversation_dedup_persist_failed", error=str(exc))
            return

        # ---- Step 3: extract (no client_id needed yet) ------------------
        try:
            extraction_outcome = await extract_only(file_bytes)
        except PipelineError as exc:
            _log.error("document_extract_failed", error=str(exc), file_id=file_id)
            await _send_user_error(
                envelope.chat_id,
                "I couldn't read this document. Could you re-upload a clearer copy?",
            )
            return

        # ---- Step 4: route on extracted buyer fields --------------------
        extraction_data = extraction_outcome.extraction
        extracted_vendor_gstin = _safe_str(extraction_data, "vendor", "gstin")
        outcome = await resolve_identity(
            session=session,
            ca_firm_id=firm.id,
            chat_id=envelope.chat_id,
            caption=caption,
            extracted_text=None,
            extracted_vendor_gstin=extracted_vendor_gstin,
            file_ids=[file_id],
            message_id=envelope.message_id,
            extraction=extraction_data,
            extraction_document_type=extraction_outcome.document_type,
        )

        # ---- Step 5b: ambiguous → informed prompt -----------------------
        if not outcome.is_resolved:
            await session.commit()
            await _send_informed_pending_prompt(
                session=session,
                ca_firm_id=firm.id,
                chat_id=envelope.chat_id,
                reply_to_message_id=envelope.message_id,
                extraction=extraction_data,
            )
            return

        assert outcome.client_id is not None  # — narrowed by is_resolved

        clients = ClientQuery(session=session, ca_firm_id=firm.id)
        client = await clients.get_by_id(outcome.client_id)
        if client is None:
            await _send_user_error(
                envelope.chat_id,
                "Routed client no longer exists. Please re-upload.",
            )
            return

        # ---- Step 5a: routed → finalise without re-extracting -----------
        await _finalize_and_summarise(
            session=session,
            firm=firm,
            client=client,
            outcome=extraction_outcome,
            file_ids=[file_id],
            chat_id=envelope.chat_id,
            reply_to_message_id=envelope.message_id,
            source_file_hash=source_file_hash,
        )


def _safe_str(payload: Any, *keys: str) -> str | None:
    """Walk a nested dict by ``keys`` and return the leaf if it's a non-empty str.

    Used for picking GSTIN / name fields out of the extractor JSON without
    sprinkling ``isinstance`` checks across every call site.
    """
    cursor: Any = payload
    for key in keys:
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(key)
    if isinstance(cursor, str) and cursor.strip():
        return cursor.strip()
    return None


# ---------------------------------------------------------------------------
# Tally ingestion (A2 — Brain Ingestion v1, ADR-014)
# ---------------------------------------------------------------------------


# Fuzzy-match floor for routing a Tally export to one client. Below this we
# refuse to guess and ask the CA to name the client (via caption).
_TALLY_CLIENT_MATCH_FLOOR: float = 0.6


async def _resolve_tally_client(
    *,
    session: Any,
    firm: CaFirm,
    company_name: str,
    caption: str | None,
) -> Client | None:
    """Route a Tally export to one client: caption wins, else company name.

    A Tally company maps to one Enma client. We try the upload caption
    first (the CA's explicit override), then the export's
    ``SVCURRENTCOMPANY``. A match must clear
    :data:`_TALLY_CLIENT_MATCH_FLOOR` and be unambiguous (the runner-up
    at least 0.05 lower) — otherwise we return ``None`` and the caller
    asks the CA to name the client.
    """
    clients_q = ClientQuery(session=session, ca_firm_id=firm.id)
    for candidate in (caption, company_name):
        if not candidate or not candidate.strip():
            continue
        ranked = await clients_q.search_by_name_ranked(
            candidate.strip(), min_similarity=0.55, limit=2
        )
        if not ranked:
            continue
        top_client, top_sim = ranked[0]
        unambiguous = len(ranked) == 1 or (ranked[1][1] < top_sim - 0.05)
        if top_sim >= _TALLY_CLIENT_MATCH_FLOOR and unambiguous:
            return top_client
    return None


async def _run_tally_ingestion(
    *,
    session: Any,
    firm: CaFirm,
    chat_id: int,
    reply_to_message_id: int | None,
    file_bytes: bytes,
    caption: str | None,
) -> None:
    """Parse an uploaded Tally export and land its vouchers as brain_events.

    Deterministic: no LLM, no tax recompute (Tally already did the math).
    Re-uploads collapse at the brain_events content-hash dedup layer, so
    the CA can safely re-send a whole month's Day Book and only new
    vouchers are added.
    """
    try:
        export = tally_import.parse_tally_export(file_bytes)
    except tally_import.TallyImportError as exc:
        _log.warning("tally_import_parse_failed", error=str(exc), chat_id=chat_id)
        await _send_user_error(
            chat_id,
            "That looked like a Tally file but I couldn't read its vouchers. "
            "Please re-export the Day Book as XML and try again.",
        )
        return

    client = await _resolve_tally_client(
        session=session,
        firm=firm,
        company_name=export.company_name,
        caption=caption,
    )
    if client is None:
        clients_q = ClientQuery(session=session, ca_firm_id=firm.id)
        active = list(await clients_q.list_active())
        company_label = safe_text(export.company_name or "(unnamed company)")
        if not active:
            await _send_user_error(
                chat_id,
                "Your firm has no active clients yet. Add a client first, "
                "then re-send the Tally file.",
            )
            return
        sample = "\n".join("• " + safe_text(c.trade_name) for c in active[:10])
        extra = "" if len(active) <= 10 else f"\n{italic(f'+ {len(active) - 10} more')}"
        html = (
            bold("Which client is this Tally export for?")
            + f"\nI couldn't match the Tally company {company_label} to one of "
            "your clients. Re-send the file with the client's name as the caption.\n"
            + sample
            + extra
        )
        try:
            await telegram.send_message(
                chat_id=chat_id,
                html_text=html,
                reply_to_message_id=reply_to_message_id,
            )
        except telegram.TelegramAPIError as exc:
            _log.error("tally_client_prompt_send_failed", error=str(exc))
        return

    rows = [
        {
            "source": "tally",
            "event_type": v.event_type,
            "dedup_key": v.dedup_key,
            "occurred_at": v.date,
            "payload": v.to_payload(),
            "client_id": client.id,
        }
        for v in export.vouchers
    ]
    brain_q = BrainEventQuery(session=session, ca_firm_id=firm.id)
    inserted, skipped = await brain_q.record_dedup_bulk(rows=rows)
    await session.commit()

    _log.info(
        "tally_ingestion_completed",
        ca_firm_id=str(firm.id),
        client_id=str(client.id),
        vouchers=len(export.vouchers),
        inserted=inserted,
        skipped=skipped,
    )

    total = len(export.vouchers)
    html = (
        bold("📊 Tally import — " + safe_text(client.trade_name))
        + f"\nVouchers in file: {total}"
        + f"\nNew: {inserted}"
        + (f"\nAlready on file: {skipped}" if skipped else "")
    )
    await messaging_factory.client_for(firm=firm).send_message(
        recipient=chat_id,
        body=RawHtml(html),
        reply_to_id=reply_to_message_id,
    )

    # L1 session memory — record the import so the supervisor can recall
    # "you imported N Tally vouchers for X" on a later question.
    try:
        await ConversationQuery(session=session, ca_firm_id=firm.id).append_turn(
            chat_id=chat_id,
            role="assistant",
            content=html,
            client_id=client.id,
            metadata={
                "kind": "tally_import",
                "vouchers": total,
                "inserted": inserted,
                "skipped": skipped,
            },
        )
        await session.commit()
    except Exception as exc:  # — memory must never break the user reply
        _log.error("tally_import_memory_persist_failed", error=str(exc))


async def _run_gstr2b_ingestion(
    *,
    session: Any,
    firm: CaFirm,
    chat_id: int,
    reply_to_message_id: int | None,
    file_bytes: bytes,
) -> None:
    """Parse an uploaded GSTR-2B JSON, ingest to brain_events, auto-reconcile.

    Routes to the client by the *recipient* GSTIN on the 2B (reliable, no
    fuzzy match needed) and derives the period from the return period.
    Then reconciles the period's books against the freshly-parsed entries
    (ADR-015). Deterministic — no LLM.
    """
    try:
        parsed = gstr2b_import.parse_gstr2b(file_bytes)
    except gstr2b_import.Gstr2bParseError as exc:
        _log.warning("gstr2b_parse_failed", error=str(exc), chat_id=chat_id)
        await _send_user_error(
            chat_id,
            "That looked like a GSTR-2B file but I couldn't read it. "
            "Please re-download the JSON from the portal and try again.",
        )
        return

    period = recon_runner.period_from_rtnprd(parsed.return_period)
    if period is None:
        await _send_user_error(
            chat_id,
            "I couldn't read the return period from that GSTR-2B file.",
        )
        return
    month, year = period

    clients_q = ClientQuery(session=session, ca_firm_id=firm.id)
    client = (
        await clients_q.get_by_gstin(parsed.recipient_gstin)
        if parsed.recipient_gstin
        else None
    )
    if client is None:
        await _send_user_error(
            chat_id,
            "I couldn't match the GSTR-2B recipient GSTIN "
            f"({safe_text(parsed.recipient_gstin or 'unknown')}) to one of your "
            "clients. Add the client with that GSTIN, then re-send the file.",
        )
        return

    # Ingest each 2B entry into brain_events (idempotent; reusable by TA-3).
    rows = [
        {
            "source": "gstn_portal",
            "event_type": "gstr2b_entry",
            "dedup_key": e.dedup_key,
            "occurred_at": datetime(
                e.invoice_date.year, e.invoice_date.month, e.invoice_date.day, tzinfo=UTC
            ),
            "payload": {**e.to_payload(), "return_period": parsed.return_period},
            "client_id": client.id,
        }
        for e in parsed.entries
    ]
    brain_q = BrainEventQuery(session=session, ca_firm_id=firm.id)
    inserted, skipped = await brain_q.record_dedup_bulk(rows=rows)
    await session.commit()
    _log.info(
        "gstr2b_ingestion_completed",
        ca_firm_id=str(firm.id),
        client_id=str(client.id),
        entries=len(parsed.entries),
        inserted=inserted,
        skipped=skipped,
    )

    # Auto-reconcile against the client's books for the return period.
    delivery = await recon_runner.reconcile_and_deliver(
        session=session,
        firm=firm,
        client=client,
        month=month,
        year=year,
        entries=list(parsed.entries),
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
    )

    await messaging_factory.client_for(firm=firm).send_message(
        recipient=chat_id,
        body=RawHtml(safe_text(delivery.summary)),
        reply_to_id=reply_to_message_id,
    )


async def _finalize_and_summarise(
    *,
    session: Any,
    firm: CaFirm,
    client: Client,
    outcome: ExtractionOutcome,
    file_ids: list[str],
    chat_id: int,
    reply_to_message_id: int | None,
    source_file_hash: str | None = None,
) -> None:
    """R2 — Persist + send the HTML summary using a cached ExtractionOutcome.

    Single helper shared by:

    * :func:`_run_document_pipeline` (initial extract-first call).
    * :func:`_resume_pipeline_for_pending_assignment` when the pending row
      carries a cached extraction (``/assign`` or inline-button confirmation
      after EXPLICIT_ASK).

    No re-OCR — the extractor only ever runs once per upload.
    """
    try:
        result = await finalize_document(
            session=session,
            ca_firm_id=firm.id,
            client_id=client.id,
            outcome=outcome,
            source_file_ids=file_ids,
            source_file_hash=source_file_hash,
        )
    except PipelineError as exc:
        _log.error("document_finalize_failed", error=str(exc))
        await _send_user_error(
            chat_id,
            "I couldn't save this document. Our team has been notified.",
        )
        return

    html = render_pipeline_summary(result)
    # W4-P2c — factory route. RawHtml preserves the existing summary
    # exactly on Telegram; WA degrades it (strips tags) automatically.
    await messaging_factory.client_for(firm=firm).send_message(
        recipient=chat_id,
        body=RawHtml(html),
        reply_to_id=reply_to_message_id,
    )

    # W3.5-e — L1 session memory. Persist the document summary as an
    # assistant turn so the supervisor LLM sees it in conversation
    # history on later questions like "is this invoice ITC claimable?"
    # or "show me the most recent invoice". Without this the LLM has
    # no way to recall what we just told the CA 60 seconds earlier.
    try:
        await ConversationQuery(session=session, ca_firm_id=firm.id).append_turn(
            chat_id=chat_id,
            role="assistant",
            content=html,
            client_id=client.id,
            metadata={
                "kind": "document_summary",
                "document_id": str(result.document_id) if result.document_id else None,
                "document_type": result.document_type,
            },
        )
        await session.commit()
    except Exception as exc:  # — memory persistence must never break the user reply
        _log.error("conversation_summary_persist_failed", error=str(exc))


async def _send_informed_pending_prompt(
    *,
    session: Any,
    ca_firm_id: Any,
    chat_id: int,
    reply_to_message_id: int | None,
    extraction: dict[str, Any] | None,
) -> None:
    """R2 — ambiguity prompt that shows the extracted buyer details.

    Previously the prompt just said "Which client is this document for?"
    with no extracted context. Now it shows the buyer's name and GSTIN
    from the extractor so the CA can confirm at a glance instead of
    re-reading the PDF.
    """
    clients = ClientQuery(session=session, ca_firm_id=ca_firm_id)
    active = list(await clients.list_active())
    if not active:
        await _send_user_error(
            chat_id,
            "Your firm has no active clients yet. "
            "Add a client with /add_client before uploading documents.",
        )
        return

    # R4 — show BOTH parties so the CA can route an outward-supply
    # invoice (their client is the vendor) without re-reading the PDF.
    vendor_name = _safe_str(extraction, "vendor", "name")
    vendor_gstin = _safe_str(extraction, "vendor", "gstin")
    buyer_name = _safe_str(extraction, "buyer", "name")
    buyer_gstin = _safe_str(extraction, "buyer", "gstin")
    header = bold("Which client is this document for?")
    detected = ""
    if vendor_name or vendor_gstin:
        detected += "\nVendor: " + safe_text(vendor_name or "(unknown)")
        if vendor_gstin:
            detected += " · GSTIN " + code(vendor_gstin)
    if buyer_name or buyer_gstin:
        detected += "\nBuyer: " + safe_text(buyer_name or "(unknown)")
        if buyer_gstin:
            detected += " · GSTIN " + code(buyer_gstin)
    if not detected:
        detected = "\nI couldn't read the parties on this invoice clearly."
    # R3 — drop the /assign slash-command syntax demand. Just reply with
    # the name. The pre-supervisor pending-assignment matcher will
    # resolve free-form text against the active client list.
    hint = "\nJust reply with the client's name."
    sample = "\n".join("• " + safe_text(c.trade_name) for c in active[:10])
    extra = "" if len(active) <= 10 else f"\n{italic(f'+ {len(active) - 10} more')}"
    html = header + detected + hint + "\n" + sample + extra
    try:
        await telegram.send_message(
            chat_id=chat_id,
            html_text=html,
            reply_to_message_id=reply_to_message_id,
        )
    except telegram.TelegramAPIError as exc:
        _log.error("pending_prompt_send_failed", error=str(exc))


async def _process_document_for_client(
    *,
    session: Any,
    firm: CaFirm,
    client: Client,
    file_ids: list[str],
    chat_id: int,
    reply_to_message_id: int | None,
) -> None:
    """Run the extraction pipeline for each file_id and reply with the summary.

    Shared by two call sites:

    * :func:`_run_document_pipeline` — the natural path, fired right after
      identity resolution succeeds.
    * :func:`_resume_pipeline_for_pending_assignment` — the recovery path,
      fired after ``/assign`` (or a natural-language assignment via the
      supervisor) resolves an open pending_assignment that was created
      because identity stage 5 (EXPLICIT_ASK) had fired.

    Errors are reported to the user as warm one-liners; the underlying
    cause is logged + captured to Sentry via the LLM/embedding services.
    """
    for file_id in file_ids:
        try:
            file_bytes = await telegram.download_file(file_id)
        except telegram.TelegramAPIError as exc:
            _log.error(
                "document_pipeline_download_failed",
                error=str(exc),
                file_id=file_id,
            )
            await _send_user_error(
                chat_id,
                "I couldn't download that file from Telegram. Could you try again?",
            )
            continue

        try:
            result = await run_pipeline(
                session=session,
                ca_firm_id=firm.id,
                client_id=client.id,
                file_bytes=file_bytes,
                source_file_ids=[file_id],
            )
        except PipelineError as exc:
            _log.error(
                "document_pipeline_failed",
                error=str(exc),
                file_id=file_id,
            )
            await _send_user_error(
                chat_id,
                "I couldn't extract this document. Our team has been notified.",
            )
            continue

        html = render_pipeline_summary(result)
        await telegram.send_message(
            chat_id=chat_id,
            html_text=html,
            reply_to_message_id=reply_to_message_id,
        )


async def _try_resolve_pending_from_text(
    *,
    session: Any,
    firm: CaFirm,
    chat_id: int,
    reply_to_message_id: int | None,
    text: str,
) -> bool:
    """R3 — agentic routing: free-form text resolves an open pending doc.

    Triggered before the slash-command branch in the command pipeline.
    Cascade:

    * No open pending_assignment for this chat → ``False`` (caller falls
      through to slash / supervisor as usual).
    * Open pending + text fuzzy-matches exactly one client at HIGH
      similarity (≥ :data:`BUYER_NAME_TRGM_HIGH_THRESHOLD`) → resolve
      the pending row + fire the cached-extraction finalize. Returns
      ``True``.
    * Open pending + ambiguous (multiple candidates ≥ 0.55) → send a
      tighter "did you mean A or B?" follow-up and return ``True`` so
      the supervisor isn't invoked for a routing reply.
    * Open pending + no match → ``False`` (the caller routes to the
      supervisor, which still has tools to handle messy phrasings).

    The caller has already done firm lookup and basic text validation.
    """
    pending_q = PendingAssignmentQuery(session=session, ca_firm_id=firm.id)
    open_rows = list(await pending_q.list_open_for_chat(chat_id=chat_id))
    if not open_rows:
        return False
    most_recent = open_rows[0]

    clients_q = ClientQuery(session=session, ca_firm_id=firm.id)
    ranked = await clients_q.search_by_name_ranked(
        text, min_similarity=BUYER_NAME_TRGM_THRESHOLD, limit=3
    )
    if not ranked:
        # No fuzzy match — let the supervisor try its luck with the LLM.
        return False

    top_client, top_sim = ranked[0]

    # Single-candidate HIGH: deterministic resolve + cached-extraction finalise.
    if (
        top_sim >= BUYER_NAME_TRGM_HIGH_THRESHOLD
        and (len(ranked) == 1 or (ranked[1][1] < top_sim - 0.05))
    ):
        resolved = await pending_q.resolve(
            pending_id=most_recent.id, client_id=top_client.id
        )
        if resolved is None:
            # Race / TTL: nothing to do, let supervisor handle it.
            return False
        await session.commit()
        await telegram.send_message(
            chat_id=chat_id,
            html_text=(
                bold("Routed to ")
                + safe_text(top_client.trade_name)
                + italic(f" (matched {int(top_sim * 100)}%)")
            ),
            reply_to_message_id=reply_to_message_id,
        )
        await _resume_pipeline_for_pending_assignment(
            session=session,
            firm=firm,
            chat_id=chat_id,
            pending_assignment_id=most_recent.id,
        )
        return True

    # MEDIUM or multiple HIGH candidates → ask informally, no syntax demanded.
    candidates_html = "\n".join(
        "• "
        + safe_text(c.trade_name)
        + italic(f" ({int(sim * 100)}% match)")
        for c, sim in ranked[:3]
    )
    body = (
        bold("Did you mean one of these?")
        + "\n"
        + candidates_html
        + "\n"
        + italic("Reply with the exact name and I'll route it there.")
    )
    try:
        await telegram.send_message(
            chat_id=chat_id,
            html_text=body,
            reply_to_message_id=reply_to_message_id,
        )
    except telegram.TelegramAPIError as exc:
        _log.error("pending_disambiguation_send_failed", error=str(exc))
    return True


async def _resume_pipeline_for_pending_assignment(
    *,
    session: Any,
    firm: CaFirm,
    chat_id: int,
    pending_assignment_id: uuid.UUID,
) -> None:
    """Fire the extraction pipeline for a freshly-resolved pending assignment.

    Called from the command pipeline after ``/assign`` succeeds (and from
    the equivalent supervisor tool when natural language resolves the
    assignment). Pulls the resolved row to read its ``file_ids`` and
    ``message_id``, then defers to :func:`_process_document_for_client`.

    The row's ``resolved_client_id`` is authoritative — the supervisor /
    command layer just set it, so this never gets to pick a client.
    """
    pending_q = PendingAssignmentQuery(session=session, ca_firm_id=firm.id)
    # We can't use ``get_open_by_id`` here because the row was just resolved.
    # Re-query by id directly so we can read file_ids + the original message_id.

    from app.db.models.pending_assignment import PendingAssignment

    stmt = pending_q._scoped_select(PendingAssignment).where(
        PendingAssignment.id == pending_assignment_id
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None or row.resolved_client_id is None:
        _log.warning(
            "pending_assignment_missing_or_unresolved",
            pending_assignment_id=str(pending_assignment_id),
        )
        return

    clients = ClientQuery(session=session, ca_firm_id=firm.id)
    client = await clients.get_by_id(row.resolved_client_id)
    if client is None:
        await _send_user_error(
            chat_id,
            "I couldn't find the routed client. Please re-upload the document.",
        )
        return

    file_ids = [str(fid) for fid in (row.file_ids or []) if fid]
    if not file_ids:
        _log.warning(
            "pending_assignment_no_file_ids",
            pending_assignment_id=str(pending_assignment_id),
        )
        return

    # R2 — cached-extraction fast path. The pending row carries the
    # extract_only output from the upload that triggered the prompt, so
    # the /assign confirmation can finalise without re-OCR'ing.
    if (
        isinstance(row.extraction, dict)
        and row.extraction
        and isinstance(row.extraction_document_type, str)
    ):
        cached_outcome = ExtractionOutcome(
            document_type=row.extraction_document_type,
            extraction=row.extraction,
            verification=None,
            stages=(),
        )
        await _finalize_and_summarise(
            session=session,
            firm=firm,
            client=client,
            outcome=cached_outcome,
            file_ids=file_ids,
            chat_id=chat_id,
            reply_to_message_id=row.message_id,
        )
        return

    # Legacy path — pending row pre-dates R2 (no cached extraction). Fall
    # back to the original download-extract-summary helper.
    await _process_document_for_client(
        session=session,
        firm=firm,
        client=client,
        file_ids=file_ids,
        chat_id=chat_id,
        reply_to_message_id=row.message_id,
    )


async def _send_pending_assignment_prompt(
    *,
    session: Any,
    ca_firm_id: Any,
    chat_id: int,
    reply_to_message_id: int | None,
) -> None:
    """Best-effort prompt: 'which client should I assign this to?'."""
    clients = ClientQuery(session=session, ca_firm_id=ca_firm_id)
    active = list(await clients.list_active())
    if not active:
        await _send_user_error(
            chat_id,
            "Your firm has no active clients yet. "
            "Add a client with /add_client before uploading documents.",
        )
        return
    sample = "\n".join("• " + safe_text(c.trade_name) for c in active[:10])
    extra = "" if len(active) <= 10 else f"\n{italic(f'+ {len(active) - 10} more')}"
    html = (
        bold("Which client is this document for?")
        + "\nJust reply with the client's name."
        + "\n"
        + sample
        + extra
    )
    try:
        await telegram.send_message(
            chat_id=chat_id,
            html_text=html,
            reply_to_message_id=reply_to_message_id,
        )
    except telegram.TelegramAPIError as exc:
        _log.error("pending_prompt_send_failed", error=str(exc))


def _extract_caption(payload: dict[str, Any]) -> str | None:
    """Pull the caption (if any) from a document envelope payload."""
    raw = payload.get("caption")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    files = payload.get("files")
    if isinstance(files, list) and files:
        first = files[0]
        if isinstance(first, dict):
            cap = first.get("caption")
            if isinstance(cap, str) and cap.strip():
                return cap.strip()
    return None


def _extract_file_id(payload: dict[str, Any]) -> str | None:
    """Pull the Telegram ``file_id`` from the envelope payload.

    Gateway envelopes carry the file_id either as ``payload.file_id``
    (single photo/document) or as ``payload.files[0].file_id`` (batches).
    """
    if isinstance(payload.get("file_id"), str):
        return str(payload["file_id"])
    files = payload.get("files")
    if isinstance(files, list) and files:
        first = files[0]
        if isinstance(first, dict) and isinstance(first.get("file_id"), str):
            return str(first["file_id"])
    return None


# ---------------------------------------------------------------------------
# Stub pipelines for non-document kinds (replaced in later phases)
# ---------------------------------------------------------------------------


async def _phase3_stub_pipeline(envelope: DecodedEnvelope) -> None:
    """Stand-in for kinds whose phase hasn't landed.

    Phase 7 replaces voice. Until then this just logs so the wiring is
    observable in tests.
    """
    _log.info(
        "background_pipeline_stub",
        kind=envelope.kind,
        chat_id=envelope.chat_id,
        message_id=envelope.message_id,
        update_id=envelope.update_id,
    )


# ---------------------------------------------------------------------------
# Command pipeline (Phase 6)
# ---------------------------------------------------------------------------


# Pattern for the confirmation message: "ENMA CONFIRM FILING <8-hex>".
_CONFIRM_REGEX: re.Pattern[str] = re.compile(
    r"^ENMA CONFIRM FILING ([0-9a-f]{8})$"
)


async def _run_command_pipeline(envelope: DecodedEnvelope) -> None:  # noqa: PLR0911, PLR0912
    """Background task for a single command (text) envelope.

    Order of precedence:

        0. /start (onboarding — runs WITHOUT a firm).
        0b. Mid-onboarding reply (runs WITHOUT a firm).
        1. ENMA APPROVE FILING <Month> <Year>  → parse + send confirm.
        2. ENMA CONFIRM FILING <hash8>         → finalize the approval.
        3. Slash command (/add_client, …)      → deterministic dispatch.
        4. Anything else                       → supervisor LLM ReAct loop.
    """
    if envelope.chat_id is None:
        return
    text = envelope.payload.get("text")
    if not isinstance(text, str) or not text.strip():
        await _send_user_error(envelope.chat_id, "Empty message.")
        return
    text = text.strip()

    factory = get_sessionmaker()
    async with factory() as session:

        # ---- 0. /start (onboarding) — BEFORE firm lookup -----------------
        if text.lower().startswith("/start"):
            payload = text[6:].strip() if len(text) > 6 else None
            html = await handle_start(session, envelope.chat_id, payload)
            await telegram.send_message(
                chat_id=envelope.chat_id,
                html_text=html,
                reply_to_message_id=envelope.message_id,
            )
            return

        # ---- 0b. Mid-onboarding reply — BEFORE firm lookup ---------------
        if is_in_onboarding(envelope.chat_id):
            html = await handle_onboarding_reply(
                session, envelope.chat_id, text
            )
            if html is not None:
                await telegram.send_message(
                    chat_id=envelope.chat_id,
                    html_text=html,
                    reply_to_message_id=envelope.message_id,
                )
                return
            # html is None → not an onboarding reply, fall through

        # ---- Firm lookup gate (unchanged) --------------------------------
        firm = await find_firm_by_admin_chat_id(session, envelope.chat_id)
        if firm is None:
            await _send_user_error(
                envelope.chat_id,
                "No firm is registered for this Telegram account. "
                "Please complete signup at https://enmalabs.in/onboarding "
                "and then tap the link from your dashboard.",
            )
            return

        # ---- 0c. Agentic routing reply (R3) ------------------------------
        # If there's an open pending_assignment waiting in this chat and
        # the user's text fuzzy-matches a client, resolve it directly —
        # NO /assign syntax required. This is the "Cursor for accounting"
        # move: the bot infers intent from context instead of demanding
        # a command.
        #
        # Skip slash commands and reserved verbs so power-users can still
        # type "/assign", "/status", "ENMA APPROVE FILING ..." even when
        # a pending doc is open.
        if (
            not is_slash_command(text)
            and not APPROVAL_REGEX.fullmatch(text)
            and not _CONFIRM_REGEX.fullmatch(text)
        ):
            handled = await _try_resolve_pending_from_text(
                session=session,
                firm=firm,
                chat_id=envelope.chat_id,
                reply_to_message_id=envelope.message_id,
                text=text,
            )
            if handled:
                return

        # ---- 1. Filing approval ------------------------------------------
        if APPROVAL_REGEX.fullmatch(text):
            await _handle_approval_parse(
                session=session,
                ca_firm_id=firm.id,
                chat_id=envelope.chat_id,
                reply_to=envelope.message_id,
                text=text,
            )
            return

        # ---- 2. Filing confirm -------------------------------------------
        confirm_match = _CONFIRM_REGEX.fullmatch(text)
        if confirm_match:
            await _handle_approval_confirm(
                session=session,
                ca_firm_id=firm.id,
                chat_id=envelope.chat_id,
                reply_to=envelope.message_id,
                hash_prefix=confirm_match.group(1),
            )
            return

        # ---- 3. Slash command --------------------------------------------
        # W2.E — slash commands are a POWER-USER SHORTCUT, not a gate.
        # Unknown /verb falls through to the supervisor as plain text so
        # the LLM can do something sensible (often: answer in natural
        # language, or fire a mutating tool that matches the intent).
        # Known verbs still dispatch directly — they remain the fastest
        # path for users who know them.
        if is_slash_command(text):
            parsed = parse_command(text)
            if parsed is None:
                _log.info(
                    "unknown_slash_falls_through_to_supervisor",
                    chat_id=envelope.chat_id,
                    text_preview=text[:64],
                )
                # Deliberately not ``return`` — fall through to the
                # supervisor (step 4) so it can interpret the unknown
                # /verb in natural language.
            else:
                result = await dispatch_command(
                    session=session,
                    ca_firm_id=firm.id,
                    chat_id=envelope.chat_id,
                    command=parsed,
                )
                await session.commit()
                await telegram.send_message(
                    chat_id=envelope.chat_id,
                    html_text=result.html,
                    reply_to_message_id=envelope.message_id,
                )
                # If /assign just resolved a pending document, fire the
                # extraction pipeline against the queued file_ids so the
                # user gets the actual extracted summary, not just
                # "Assigned — ...".
                if result.success and result.pending_assignment_resolved is not None:
                    await _resume_pipeline_for_pending_assignment(
                        session=session,
                        firm=firm,
                        chat_id=envelope.chat_id,
                        pending_assignment_id=result.pending_assignment_resolved,
                    )
                return

        # ---- 4. Supervisor (free-form) -----------------------------------
        await _handle_supervisor(
            session=session,
            firm=firm,
            chat_id=envelope.chat_id,
            reply_to=envelope.message_id,
            message_id=envelope.message_id,
            text=text,
            channel=envelope.payload.get("channel"),
        )


async def _handle_approval_parse(
    *,
    session: Any,
    ca_firm_id: Any,
    chat_id: int,
    reply_to: int | None,
    text: str,
) -> None:
    """Parse ENMA APPROVE FILING and reply with the confirm prompt."""
    try:
        intent = await parse_approval_intent(
            session=session, ca_firm_id=ca_firm_id, text=text
        )
    except InvalidApprovalString:
        await _send_user_error(chat_id, "Could not parse the approval string.")
        return
    except FilingPeriodAlreadyLocked as exc:
        await _send_user_error(
            chat_id,
            f"Filing for {exc.month:02d}/{exc.year} is already locked.",
        )
        return

    hash8 = intent.snapshot_hash[:8]
    body = (
        bold("Filing summary — confirm to lock")
        + "\n"
        + f"Period: {intent.month:02d}/{intent.year}\n"
        + f"Documents: {intent.document_count}\n"
        + "Taxable value: " + code(str(intent.total_taxable_value)) + "\n"
        + "Total tax: " + code(str(intent.total_tax)) + "\n\n"
        + "Reply with " + code(f"ENMA CONFIRM FILING {hash8}") + " to finalise."
    )
    await telegram.send_message(
        chat_id=chat_id,
        html_text=body,
        reply_to_message_id=reply_to,
    )


async def _handle_approval_confirm(
    *,
    session: Any,
    ca_firm_id: Any,
    chat_id: int,
    reply_to: int | None,
    hash_prefix: str,
) -> None:
    """Re-derive every open period's snapshot and finalise on prefix match.

    We re-derive every open period (≤ 12 monthly periods) and check the
    8-char hash prefix. This avoids needing transient state — the hash
    *is* the state. A drifted snapshot since the original parse will
    naturally fail to match, which is the correct safety behaviour.
    """
    # Iterate the documents grouped by period for this firm. We scan a
    # bounded window of recent periods rather than every period ever.
    from app.db.queries.documents import DocumentQuery

    docs_q = DocumentQuery(session=session, ca_firm_id=ca_firm_id)
    all_docs = list(await docs_q.list_all())
    periods: set[tuple[int, int]] = set()
    for d in all_docs:
        if d.filing_period_month and d.filing_period_year:
            periods.add((d.filing_period_month, d.filing_period_year))

    for month, year in periods:
        approval_text = f"ENMA APPROVE FILING {_MONTH_NAMES[month - 1]} {year}"
        try:
            intent = await parse_approval_intent(
                session=session, ca_firm_id=ca_firm_id, text=approval_text
            )
        except (InvalidApprovalString, FilingPeriodAlreadyLocked):
            continue
        if not intent.snapshot_hash.startswith(hash_prefix):
            continue
        # Match — finalise.
        try:
            result = await finalize_approval(
                session=session,
                ca_firm_id=ca_firm_id,
                approved_by_chat_id=chat_id,
                text=approval_text,
                expected_snapshot_hash=intent.snapshot_hash,
            )
        except (InvalidApprovalString, FilingPeriodAlreadyLocked) as exc:
            await _send_user_error(chat_id, f"Could not finalise: {exc}")
            return
        await session.commit()
        await telegram.send_message(
            chat_id=chat_id,
            html_text=(
                bold("Filing locked")
                + f"\nPeriod {result.month:02d}/{result.year}\n"
                + "Snapshot hash: "
                + code(result.snapshot_hash[:16])
            ),
            reply_to_message_id=reply_to,
        )
        return

    await _send_user_error(
        chat_id,
        "No matching pending filing found. Re-send the APPROVE line first.",
    )


async def _handle_supervisor(
    *,
    session: Any,
    firm: CaFirm,
    chat_id: int,
    reply_to: int | None,
    message_id: int | None,
    text: str,
    channel: Any = None,
) -> None:
    """Run the supervisor agent and reply with its answer."""
    ca_firm_id = firm.id
    convs = ConversationQuery(session=session, ca_firm_id=ca_firm_id)
    active_client_id = await convs.get_last_client_id(chat_id=chat_id)

    recent = await convs.list_recent_live(
        chat_id=chat_id, limit=RECENT_WINDOW_TURNS
    )
    history = [
        {"role": t.role, "content": t.content}
        for t in recent
        if t.role in ("user", "assistant", "system")
    ]

    # Log the inbound turn before the LLM call so it shows up in history
    # on the next request even if the LLM call fails.
    await convs.append_turn(
        chat_id=chat_id,
        role="user",
        content=text,
        message_id=message_id,
        client_id=active_client_id,
    )
    await session.commit()

    ctx = SupervisorContext(
        session=session,
        ca_firm_id=ca_firm_id,
        chat_id=chat_id,
        active_client_id=active_client_id,
        firm_name=firm.firm_name,
        ca_name=firm.ca_name,
    )
    try:
        reply = await run_supervisor(ctx=ctx, user_text=text, history=history)
    except LLMError as exc:
        _log.error("supervisor_failed", error=str(exc))
        await _send_user_error(chat_id, "I could not reach the language model.")
        return

    await convs.append_turn(
        chat_id=chat_id,
        role="assistant",
        content=reply.text,
        client_id=active_client_id,
    )
    await convs.maybe_compact(chat_id=chat_id)
    await session.commit()

    # P0 — agentic_trajectories: one row per supervisor turn. Wrapped
    # in best-effort so a logging failure cannot silence a real reply.
    # The convs work above is already committed, so a rollback here
    # only discards the trajectory row.
    try:
        trajectories = TrajectoryQuery(session=session, ca_firm_id=ca_firm_id)
        await trajectories.record_turn(
            chat_id=chat_id,
            user_text=text,
            tool_calls_made=list(reply.tool_call_log),
            final_reply=reply.text,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        _log.warning(
            "trajectory_record_failed",
            error=str(exc),
            chat_id=chat_id,
        )

    # W4-P3 — reply via the channel the inbound envelope arrived on, not
    # the firm-level default. Lets a firm with both channels live answer
    # Telegram messages on TG and WhatsApp messages on WA in the same
    # session without state. ``channel`` is None for legacy / unstamped
    # envelopes; the helper falls back to firm.primary_channel.
    reply_html = safe_text(reply.text) if reply.text else italic("(no reply)")
    await messaging_factory.client_for_envelope(
        envelope_payload={"channel": channel} if channel is not None else {},
        firm=firm,
    ).send_message(
        recipient=chat_id,
        body=RawHtml(reply_html),
        reply_to_id=reply_to,
    )


_MONTH_NAMES: tuple[str, ...] = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


# ---------------------------------------------------------------------------
# ACK helpers
# ---------------------------------------------------------------------------


# Only document/voice ACKs are worth showing — those pipelines run for
# 5-30s. Command and callback envelopes return within ~2s; an ACK plus
# the real reply just clutters the chat. Drop the ACK for those kinds.
_ACK_TEXT: dict[str, str] = {
    "document": "Processing your document…",
    "document_batch": "Processing your document batch…",
    "voice": "Transcribing your voice note…",
}


def _ack_html(kind: str) -> str | None:
    text = _ACK_TEXT.get(kind)
    if text is None:
        return None
    # All outbound text goes through the HTML helpers — no string interpolation.
    return italic(text)


async def _send_ack(envelope: DecodedEnvelope) -> None:
    """Best-effort ACK. Re-raises on failure so the route returns 502.

    Quiet for fast envelope kinds (``command``, ``callback``) — see
    :data:`_ACK_TEXT` for the kinds we still ACK.
    """
    if envelope.chat_id is None:
        # Nothing to ACK to — common for cron-spawned envelopes.
        return
    html = _ack_html(envelope.kind)
    if html is None:
        return
    await telegram.send_message(
        chat_id=envelope.chat_id,
        html_text=html,
        reply_to_message_id=envelope.message_id,
    )


async def _send_user_error(chat_id: int, message: str) -> None:
    """Send a user-facing HTML error message — best-effort, never raises."""
    try:
        await telegram.send_message(
            chat_id=chat_id,
            html_text=italic(safe_text(message)),
        )
    except telegram.TelegramAPIError as exc:
        _log.error("user_error_send_failed", error=str(exc), chat_id=chat_id)


# ---------------------------------------------------------------------------
# Per-kind background dispatcher
# ---------------------------------------------------------------------------


def _coroutine_for_kind(envelope: DecodedEnvelope):  # type: ignore[no-untyped-def]
    """Pick the right background coroutine for a given envelope kind."""
    if envelope.kind == "document":
        return _run_document_pipeline(envelope)
    if envelope.kind == "command":
        return _run_command_pipeline(envelope)
    if envelope.kind == "voice":
        return _run_voice_pipeline(envelope)
    return _phase3_stub_pipeline(envelope)


# ---------------------------------------------------------------------------
# Voice pipeline (Phase 7)
# ---------------------------------------------------------------------------


async def _run_voice_pipeline(envelope: DecodedEnvelope) -> None:
    """ADR-007 §Decision 5 — voice = wrapper around the command pipeline.

    Steps:
      1. Pull the OGG ``file_id`` from the payload.
      2. Download the bytes from Telegram.
      3. Transcribe via Whisper.
      4. Echo the transcript to the user (trust + auditability).
      5. Synthesize a ``command`` envelope and re-enter
         :func:`_run_command_pipeline`.
    """
    from app.services import whisper

    if envelope.chat_id is None:
        return

    file_id = _extract_file_id(envelope.payload)
    if file_id is None:
        await _send_user_error(envelope.chat_id, "Could not find a voice note in the message.")
        return

    try:
        ogg = await telegram.download_file(file_id)
    except telegram.TelegramAPIError as exc:
        _log.error("voice_download_failed", error=str(exc))
        await _send_user_error(
            envelope.chat_id, "Could not download the voice note from Telegram."
        )
        return

    try:
        transcript = await whisper.transcribe_ogg(ogg)
    except whisper.WhisperNotConfigured:
        await _send_user_error(
            envelope.chat_id,
            "Voice notes are not enabled on this deployment.",
        )
        return
    except whisper.WhisperError as exc:
        _log.error("voice_transcription_failed", error=str(exc))
        await _send_user_error(
            envelope.chat_id, "Could not transcribe the voice note. Please retry."
        )
        return

    if not transcript.text.strip():
        await _send_user_error(envelope.chat_id, "The voice note was empty.")
        return

    # Echo the transcript so the CA can see what we heard.
    try:
        await telegram.send_message(
            chat_id=envelope.chat_id,
            html_text=italic("Heard: ") + safe_text(transcript.text),
            reply_to_message_id=envelope.message_id,
        )
    except telegram.TelegramAPIError as exc:
        _log.error("voice_echo_failed", error=str(exc))

    # Synthesize a command envelope and re-enter the supervisor path.
    synthetic = envelope.model_copy(
        update={
            "kind": "command",
            "payload": {"text": transcript.text, "entities": []},
        }
    )
    await _run_command_pipeline(synthetic)


# ---------------------------------------------------------------------------
# Shared handler body
# ---------------------------------------------------------------------------


def _accepted_body(verdict: IdempotencyVerdict) -> dict[str, Any]:
    return {
        "accepted": True,
        "duplicate": False,
        "log_id": str(verdict.log_id) if verdict.log_id else None,
    }


def _duplicate_body() -> dict[str, Any]:
    return {"accepted": True, "duplicate": True, "log_id": None}


async def _process_envelope(envelope: DecodedEnvelope, verdict: IdempotencyVerdict) -> Response:
    """Common code path for every worker route."""
    if verdict.is_duplicate:
        return ORJSONResponse(status_code=status.HTTP_200_OK, content=_duplicate_body())

    # Schedule the pipeline FIRST so it starts running while we ACK.
    registry = get_registry()
    registry.spawn(
        _coroutine_for_kind(envelope),
        name=f"pipeline:{envelope.kind}:{envelope.message_id or 'none'}",
        context={
            "kind": envelope.kind,
            "chat_id": envelope.chat_id,
            "message_id": envelope.message_id,
        },
    )

    try:
        await _send_ack(envelope)
    except telegram.TelegramAPIError as exc:
        # The gateway will retry; idempotency makes that retry a no-op for DB
        # state, but the ACK is re-attempted (which is what we want).
        _log.error(
            "telegram_ack_failed",
            kind=envelope.kind,
            chat_id=envelope.chat_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="upstream telegram api failed",
        ) from exc

    return ORJSONResponse(status_code=status.HTTP_202_ACCEPTED, content=_accepted_body(verdict))


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


def _assert_kind(envelope: DecodedEnvelope, expected: str) -> None:
    """Sanity check: the URL kind must match the envelope kind."""
    if envelope.kind != expected:
        # 400 — the gateway dispatched to the wrong route.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"envelope kind {envelope.kind!r} does not match route {expected!r}"),
        )


# Rate limiting: the global default (100/minute via RATE_LIMIT_DEFAULT) is
# enforced by SlowAPIMiddleware installed in create_app(). Per-route tighter
# policies live as constants in app/api/middleware/rate_limit.py — they will
# be wired in via a Depends-based limiter once we either migrate to
# fastapi-limiter or write a custom check, since slowapi's @limit decorator
# breaks FastAPI's Annotated[..., Depends(...)] dependency resolution.


@router.post("/document", summary="Single document envelope")
async def worker_document(envelope: VerifiedEnvelopeDep, verdict: IdempotencyDep) -> Response:
    _assert_kind(envelope, "document")
    return await _process_envelope(envelope, verdict)


@router.post("/document-batch", summary="Media-group document batch")
async def worker_document_batch(envelope: VerifiedEnvelopeDep, verdict: IdempotencyDep) -> Response:
    _assert_kind(envelope, "document_batch")
    return await _process_envelope(envelope, verdict)


@router.post("/command", summary="Text command")
async def worker_command(envelope: VerifiedEnvelopeDep, verdict: IdempotencyDep) -> Response:
    _assert_kind(envelope, "command")
    return await _process_envelope(envelope, verdict)


@router.post("/voice", summary="Voice note")
async def worker_voice(envelope: VerifiedEnvelopeDep, verdict: IdempotencyDep) -> Response:
    _assert_kind(envelope, "voice")
    return await _process_envelope(envelope, verdict)


@router.post("/callback", summary="Inline-button callback")
async def worker_callback(envelope: VerifiedEnvelopeDep, verdict: IdempotencyDep) -> Response:
    _assert_kind(envelope, "callback")
    return await _process_envelope(envelope, verdict)


__all__ = [
    "router",
    # exported for tests:
    "_phase3_stub_pipeline",
    "_run_document_pipeline",
    # exported so audit tooling can grep:
    "bold",
    "safe_text",
]
