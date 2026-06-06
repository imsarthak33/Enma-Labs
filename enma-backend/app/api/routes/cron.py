"""Cron-triggered routes — task heartbeat, morning briefing, client chase.

ADR-007 §Decision 3 — sibling endpoints with their own envelope verifier
that mirrors the worker verifier on HMAC/freshness but rejects user-kind
envelopes. Idempotency is shared with the worker route via the synthetic
``(0, scheduled_at_minute)`` key (§Decision 2).

All three routes fan out across firms with :func:`list_all_firms`; every
per-firm side-effect goes through a tenant-scoped query class. The
fan-out loop is the *only* sanctioned cross-firm read in the codebase.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, status
from fastapi.responses import ORJSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.briefing import BriefingData, build_briefing_html
from app.api.middleware.envelope_verify import (
    DecodedEnvelope,
    VerifiedCronEnvelopeDep,
)
from app.api.middleware.idempotency import IdempotencyDep, IdempotencyVerdict
from app.db.models.firm import CaFirm
from app.db.queries.clients import ClientQuery
from app.db.queries.documents import DocumentQuery
from app.db.queries.firms import list_all_firms
from app.db.queries.idempotency import (
    DEFAULT_CLEANUP_AGE_HOURS,
    cleanup_idempotency_log,
)
from app.db.queries.notifications import (
    CHASE_COOLDOWN_DAYS,
    NotificationQuery,
)
from app.db.queries.tasks import TaskQuery
from app.db.session import get_sessionmaker
from app.formatting.telegram_html import bold, code, italic, safe_text
from app.logging_setup import get_logger
from app.services import scraper, telegram
from app.utils.background import get_registry
from app.utils.date_utils import FilingPeriod, now_ist

router = APIRouter(prefix="/worker/cron", tags=["cron"])

_log = get_logger(__name__)

# Hard cap so a runaway loop can't blast Telegram. ADR-007 §Decision 4.
CLIENT_CHASE_BATCH_CAP: int = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _accepted_body(verdict: IdempotencyVerdict) -> dict[str, Any]:
    return {
        "accepted": True,
        "duplicate": False,
        "log_id": str(verdict.log_id) if verdict.log_id else None,
    }


def _duplicate_body() -> dict[str, Any]:
    return {"accepted": True, "duplicate": True, "log_id": None}


def _next_period(period: FilingPeriod) -> FilingPeriod:
    """Return the calendar month after ``period``."""
    if period.month == 12:
        return FilingPeriod(year=period.year + 1, month=1)
    return FilingPeriod(year=period.year, month=period.month + 1)


def _spawn(coro: Any, name: str) -> None:
    """Hand a coroutine to the background registry with a stable name."""
    get_registry().spawn(coro, name=name, context={"name": name})


# ---------------------------------------------------------------------------
# Per-firm coroutines
# ---------------------------------------------------------------------------


async def _heartbeat_for_firm(firm: CaFirm) -> None:
    """Notify the firm admin about every overdue task whose cooldown expired."""
    factory = get_sessionmaker()
    async with factory() as session:
        tasks_q = TaskQuery(session=session, ca_firm_id=firm.id)
        due = list(await tasks_q.list_due_for_heartbeat())
        if not due:
            return
        for task in due:
            html = (
                bold("Overdue task")
                + "\n"
                + safe_text(task.title)
                + (
                    " — due " + safe_text(task.due_at.strftime("%d-%b-%Y"))
                    if task.due_at is not None
                    else ""
                )
            )
            try:
                await telegram.send_message(chat_id=firm.admin_chat_id, html_text=html)
            except telegram.TelegramAPIError as exc:
                _log.warning("heartbeat_send_failed", task_id=str(task.id), error=str(exc))
                continue
            await tasks_q.mark_notified(task.id)
        await session.commit()


async def _briefing_for_firm(firm: CaFirm) -> None:
    """Build and send the morning brief for a single firm."""
    factory = get_sessionmaker()
    today = now_ist()
    current_period = FilingPeriod.from_date(today.date())
    upcoming_period = _next_period(current_period)

    # News fetch is cached across firms, so the cost is one Serper call per day.
    try:
        news = await scraper.fetch_compliance_news()
    except scraper.ScraperError:
        news = ()

    async with factory() as session:
        tasks_q = TaskQuery(session=session, ca_firm_id=firm.id)
        docs_q = DocumentQuery(session=session, ca_firm_id=firm.id)
        open_tasks = list(await tasks_q.list_open())
        overdue_tasks = list(await tasks_q.list_overdue())
        current_docs = list(
            await docs_q.list_by_filing_period(year=current_period.year, month=current_period.month)
        )
        upcoming_docs = list(
            await docs_q.list_by_filing_period(
                year=upcoming_period.year, month=upcoming_period.month
            )
        )

    data = BriefingData(
        firm_name=firm.firm_name,
        as_of_ist=today,
        open_tasks=open_tasks,
        overdue_tasks=overdue_tasks,
        current_period=current_period,
        upcoming_period=upcoming_period,
        current_period_doc_count=len(current_docs),
        upcoming_period_doc_count=len(upcoming_docs),
        news=news,
    )
    html = build_briefing_html(data)
    try:
        await telegram.send_message(chat_id=firm.admin_chat_id, html_text=html)
    except telegram.TelegramAPIError as exc:
        _log.warning("briefing_send_failed", firm_id=str(firm.id), error=str(exc))


async def _chase_for_firm(firm: CaFirm, *, batch_cap: int = CLIENT_CHASE_BATCH_CAP) -> None:
    """Identify clients missing docs for the upcoming period; ping ≤ batch_cap."""
    factory = get_sessionmaker()
    next_period = _next_period(FilingPeriod.from_date(now_ist().date()))
    async with factory() as session:
        clients_q = ClientQuery(session=session, ca_firm_id=firm.id)
        notifications_q = NotificationQuery(session=session, ca_firm_id=firm.id)

        candidates = list(
            await clients_q.list_missing_filing_docs(month=next_period.month, year=next_period.year)
        )
        sent = 0
        for client in candidates:
            if sent >= batch_cap:
                break
            if await notifications_q.was_recently_notified(client_id=client.id):
                continue
            html = (
                bold("Document chase")
                + "\n"
                + safe_text(client.trade_name)
                + " — no documents yet for filing "
                + code(f"{next_period.month:02d}/{next_period.year}")
                + ".\n"
                + italic(f"Cooldown {CHASE_COOLDOWN_DAYS} days before the next chase.")
            )
            try:
                await telegram.send_message(chat_id=firm.admin_chat_id, html_text=html)
            except telegram.TelegramAPIError as exc:
                _log.warning(
                    "chase_send_failed",
                    firm_id=str(firm.id),
                    client_id=str(client.id),
                    error=str(exc),
                )
                continue
            await notifications_q.record_notification(client_id=client.id)
            sent += 1
        await session.commit()


# ---------------------------------------------------------------------------
# Fan-out drivers
# ---------------------------------------------------------------------------


async def _fanout(per_firm: Any, *, label: str) -> None:
    """Iterate all firms and run ``per_firm(firm)`` for each, serially.

    Serial is intentional — cron jobs are not latency-sensitive and
    sequencing keeps Telegram rate-limit pressure predictable. If we
    ever need parallelism, swap this for an ``asyncio.gather`` with a
    small semaphore.
    """
    factory = get_sessionmaker()
    async with factory() as session:
        session_typed: AsyncSession = session
        firms = list(await list_all_firms(session_typed))
    _log.info("cron_fanout_start", label=label, firm_count=len(firms))
    for firm in firms:
        try:
            await per_firm(firm)
        except Exception as exc:  # — log + continue, never let one firm break others
            _log.error(
                "cron_per_firm_failed",
                label=label,
                firm_id=str(firm.id),
                error=str(exc),
            )
            continue


async def _run_heartbeat(_envelope: DecodedEnvelope) -> None:
    await _fanout(_heartbeat_for_firm, label="task_heartbeat")


async def _run_briefing(_envelope: DecodedEnvelope) -> None:
    await _fanout(_briefing_for_firm, label="morning_briefing")


async def _run_chase(_envelope: DecodedEnvelope) -> None:
    await _fanout(_chase_for_firm, label="client_chase")


async def _run_idempotency_cleanup(_envelope: DecodedEnvelope) -> None:
    """Daily prune of ``idempotency_log`` entries older than 72 hours.

    Spec §2.4. Not a fan-out — the idempotency_log is global (no ca_firm_id
    filter applies to the row at insert time, by design — see
    ``app/db/queries/idempotency.py``).
    """
    factory = get_sessionmaker()
    async with factory() as session:
        deleted = await cleanup_idempotency_log(
            session,
            max_age_hours=DEFAULT_CLEANUP_AGE_HOURS,
        )
    _log.info("idempotency_cleanup_complete", deleted=deleted)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _assert_kind(envelope: DecodedEnvelope, expected: str) -> None:
    if envelope.kind != expected:
        # Cron verifier already accepts only cron kinds; this catches
        # gateway routing bugs (POSTing to the wrong path).
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"envelope kind {envelope.kind!r} does not match route {expected!r}",
        )


async def _process_cron(
    envelope: DecodedEnvelope,
    verdict: IdempotencyVerdict,
    runner: Any,
    label: str,
) -> Response:
    if verdict.is_duplicate:
        return ORJSONResponse(status_code=status.HTTP_200_OK, content=_duplicate_body())
    _spawn(runner(envelope), name=f"cron:{label}")
    return ORJSONResponse(status_code=status.HTTP_202_ACCEPTED, content=_accepted_body(verdict))


# Rate limiting: cron endpoints inherit the global 100/minute default from
# SlowAPIMiddleware. CRON_RATE_LIMIT (5/minute) is reserved as a per-route
# tighter policy for the planned Depends-based limiter migration.


@router.post("/task-heartbeat", summary="Cron — overdue task heartbeat")
async def cron_task_heartbeat(
    envelope: VerifiedCronEnvelopeDep,
    verdict: IdempotencyDep,
) -> Response:
    _assert_kind(envelope, "cron_task_heartbeat")
    return await _process_cron(envelope, verdict, _run_heartbeat, "task_heartbeat")


@router.post("/morning-briefing", summary="Cron — daily firm digest")
async def cron_morning_briefing(
    envelope: VerifiedCronEnvelopeDep,
    verdict: IdempotencyDep,
) -> Response:
    _assert_kind(envelope, "cron_morning_briefing")
    return await _process_cron(envelope, verdict, _run_briefing, "morning_briefing")


@router.post("/client-chase", summary="Cron — 28th-of-month client chase")
async def cron_client_chase(
    envelope: VerifiedCronEnvelopeDep,
    verdict: IdempotencyDep,
) -> Response:
    _assert_kind(envelope, "cron_client_chase")
    return await _process_cron(envelope, verdict, _run_chase, "client_chase")


@router.post(
    "/idempotency-cleanup",
    summary="Cron — daily idempotency_log cleanup (72hr TTL)",
)
async def cron_idempotency_cleanup(
    envelope: VerifiedCronEnvelopeDep,
    verdict: IdempotencyDep,
) -> Response:
    _assert_kind(envelope, "cron_idempotency_cleanup")
    return await _process_cron(envelope, verdict, _run_idempotency_cleanup, "idempotency_cleanup")


__all__ = [
    "CLIENT_CHASE_BATCH_CAP",
    "_briefing_for_firm",
    "_chase_for_firm",
    "_heartbeat_for_firm",
    "router",
]


# Unused — silence ruff. ``asyncio`` is reserved for future parallel fan-out.
_ = asyncio
