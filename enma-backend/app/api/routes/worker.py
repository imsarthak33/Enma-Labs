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

import re
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import ORJSONResponse, Response

from app.agents.commands import (
    dispatch_command,
    is_slash_command,
    parse_command,
)
from app.agents.onboarding import (
    handle_onboarding_reply,
    handle_start,
    is_in_onboarding,
)
from app.agents.filing_approval import (
    APPROVAL_REGEX,
    FilingPeriodAlreadyLocked,
    InvalidApprovalString,
    finalize_approval,
    parse_approval_intent,
)
from app.agents.pipeline import PipelineError, run_pipeline
from app.agents.supervisor import SupervisorContext, run_supervisor
from app.api.middleware.envelope_verify import DecodedEnvelope, VerifiedEnvelopeDep
from app.api.middleware.idempotency import IdempotencyDep, IdempotencyVerdict
from app.db.queries.clients import ClientQuery
from app.db.queries.conversations import RECENT_WINDOW_TURNS, ConversationQuery
from app.db.queries.firms import find_firm_by_admin_chat_id
from app.db.session import get_sessionmaker
from app.formatting.pipeline_summary import render_pipeline_summary
from app.formatting.telegram_html import bold, code, italic, safe_text
from app.identity import resolve_identity
from app.logging_setup import get_logger
from app.services import telegram
from app.services.llm import LLMError
from app.utils.background import get_registry

router = APIRouter(prefix="/worker", tags=["worker"])

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Document pipeline (Phase 4)
# ---------------------------------------------------------------------------


async def _run_document_pipeline(envelope: DecodedEnvelope) -> None:  # noqa: PLR0911
    """Background task for a single document envelope.

    Steps (Phase 6):
      1. Resolve firm by ``chat_id``.
      2. Run the 5-stage identity cascade. If stage 5 fires, queue a
         pending_assignment and ask the CA via Telegram.
      3. Download the file from Telegram.
      4. Run the extraction pipeline.
      5. Send an HTML summary back to the user.

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

        outcome = await resolve_identity(
            session=session,
            ca_firm_id=firm.id,
            chat_id=envelope.chat_id,
            caption=caption,
            extracted_text=None,
            extracted_vendor_gstin=None,
            file_ids=[file_id],
            message_id=envelope.message_id,
        )

        if not outcome.is_resolved:
            await session.commit()
            await _send_pending_assignment_prompt(
                session=session,
                ca_firm_id=firm.id,
                chat_id=envelope.chat_id,
                reply_to_message_id=envelope.message_id,
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

        # Download file bytes from Telegram.
        try:
            file_bytes = await telegram.download_file(file_id)
        except telegram.TelegramAPIError as exc:
            _log.error("document_pipeline_download_failed", error=str(exc))
            await _send_user_error(envelope.chat_id, "Could not download the file from Telegram.")
            return

        # Run the pipeline.
        try:
            result = await run_pipeline(
                session=session,
                ca_firm_id=firm.id,
                client_id=client.id,
                file_bytes=file_bytes,
                source_file_ids=[file_id],
            )
        except PipelineError as exc:
            _log.error("document_pipeline_failed", error=str(exc))
            await _send_user_error(
                envelope.chat_id,
                f"Document processing failed: {exc}",
            )
            return

    # Pipeline returned; send the HTML summary.
    html = render_pipeline_summary(result)
    await telegram.send_message(
        chat_id=envelope.chat_id,
        html_text=html,
        reply_to_message_id=envelope.message_id,
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
        + "\nReply with "
        + code('/assign "Client Name"')
        + ".\n"
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


async def _run_command_pipeline(envelope: DecodedEnvelope) -> None:  # noqa: PLR0911
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
            html = await handle_start(session, envelope.chat_id)
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
                "Type /start to register your firm.",
            )
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
        if is_slash_command(text):
            parsed = parse_command(text)
            if parsed is None:
                await _send_user_error(
                    envelope.chat_id,
                    "Unknown command. Try /list_clients or /status.",
                )
                return
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
            return

        # ---- 4. Supervisor (free-form) -----------------------------------
        await _handle_supervisor(
            session=session,
            ca_firm_id=firm.id,
            chat_id=envelope.chat_id,
            reply_to=envelope.message_id,
            message_id=envelope.message_id,
            text=text,
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
    ca_firm_id: Any,
    chat_id: int,
    reply_to: int | None,
    message_id: int | None,
    text: str,
) -> None:
    """Run the supervisor agent and reply with its answer."""
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

    await telegram.send_message(
        chat_id=chat_id,
        html_text=safe_text(reply.text) if reply.text else italic("(no reply)"),
        reply_to_message_id=reply_to,
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


_ACK_TEXT: dict[str, str] = {
    "document": "Processing your document…",
    "document_batch": "Processing your document batch…",
    "command": "Working on it…",
    "voice": "Transcribing your voice note…",
    "callback": "Got it.",
}


def _ack_html(kind: str) -> str:
    text = _ACK_TEXT.get(kind, "Working on it…")
    # All outbound text goes through the HTML helpers — no string interpolation.
    return italic(text)


async def _send_ack(envelope: DecodedEnvelope) -> None:
    """Best-effort ACK. Re-raises on failure so the route returns 502."""
    if envelope.chat_id is None:
        # Nothing to ACK to — common for cron-spawned envelopes.
        return
    await telegram.send_message(
        chat_id=envelope.chat_id,
        html_text=_ack_html(envelope.kind),
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
