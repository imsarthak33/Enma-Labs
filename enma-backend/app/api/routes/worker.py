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

from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import ORJSONResponse, Response

from app.agents.pipeline import PipelineError, run_pipeline
from app.api.middleware.envelope_verify import DecodedEnvelope, VerifiedEnvelopeDep
from app.api.middleware.idempotency import IdempotencyDep, IdempotencyVerdict
from app.db.queries.clients import ClientQuery
from app.db.queries.firms import find_firm_by_admin_chat_id
from app.db.session import get_sessionmaker
from app.formatting.pipeline_summary import render_pipeline_summary
from app.formatting.telegram_html import bold, italic, safe_text
from app.logging_setup import get_logger
from app.services import telegram
from app.utils.background import get_registry

router = APIRouter(prefix="/worker", tags=["worker"])

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Document pipeline (Phase 4)
# ---------------------------------------------------------------------------


async def _run_document_pipeline(envelope: DecodedEnvelope) -> None:
    """Background task for a single document envelope.

    Steps:
      1. Resolve firm by ``chat_id``.
      2. Pick the first active client for that firm.
         (Phase 4 stub — Phase 6 replaces with the 5-stage identity cascade.)
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

        clients = ClientQuery(session=session, ca_firm_id=firm.id)
        client = await clients.first_active()
        if client is None:
            await _send_user_error(
                envelope.chat_id,
                "Your firm has no active clients yet. "
                "Add a client with /add_client before uploading documents.",
            )
            return

        # Download file bytes from Telegram.
        try:
            file_bytes = await telegram.download_file(file_id)
        except telegram.TelegramAPIError as exc:
            _log.error("document_pipeline_download_failed", error=str(exc))
            await _send_user_error(
                envelope.chat_id, "Could not download the file from Telegram."
            )
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

    Phase 6 replaces the command/callback paths; Phase 7 replaces voice.
    Until then this just logs so the wiring is observable in tests.
    """
    _log.info(
        "background_pipeline_stub",
        kind=envelope.kind,
        chat_id=envelope.chat_id,
        message_id=envelope.message_id,
        update_id=envelope.update_id,
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
    return _phase3_stub_pipeline(envelope)


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


async def _process_envelope(
    envelope: DecodedEnvelope, verdict: IdempotencyVerdict
) -> Response:
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

    return ORJSONResponse(
        status_code=status.HTTP_202_ACCEPTED, content=_accepted_body(verdict)
    )


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


def _assert_kind(envelope: DecodedEnvelope, expected: str) -> None:
    """Sanity check: the URL kind must match the envelope kind."""
    if envelope.kind != expected:
        # 400 — the gateway dispatched to the wrong route.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"envelope kind {envelope.kind!r} does not match route {expected!r}"
            ),
        )


@router.post("/document", summary="Single document envelope")
async def worker_document(
    envelope: VerifiedEnvelopeDep, verdict: IdempotencyDep
) -> Response:
    _assert_kind(envelope, "document")
    return await _process_envelope(envelope, verdict)


@router.post("/document-batch", summary="Media-group document batch")
async def worker_document_batch(
    envelope: VerifiedEnvelopeDep, verdict: IdempotencyDep
) -> Response:
    _assert_kind(envelope, "document_batch")
    return await _process_envelope(envelope, verdict)


@router.post("/command", summary="Text command")
async def worker_command(
    envelope: VerifiedEnvelopeDep, verdict: IdempotencyDep
) -> Response:
    _assert_kind(envelope, "command")
    return await _process_envelope(envelope, verdict)


@router.post("/voice", summary="Voice note")
async def worker_voice(
    envelope: VerifiedEnvelopeDep, verdict: IdempotencyDep
) -> Response:
    _assert_kind(envelope, "voice")
    return await _process_envelope(envelope, verdict)


@router.post("/callback", summary="Inline-button callback")
async def worker_callback(
    envelope: VerifiedEnvelopeDep, verdict: IdempotencyDep
) -> Response:
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
