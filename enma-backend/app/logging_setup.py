"""Structured logging via structlog.

Single entrypoint: ``configure_logging()`` is called once during FastAPI
lifespan startup. After that, ``structlog.get_logger(__name__)`` returns
loggers that share the same processor chain.

Two renderers ship out of the box, selected by ``settings.log_format``:

* ``console`` — :class:`structlog.dev.ConsoleRenderer` (colorless). Plain
  text lines like ``2026-06-14 12:34:56 [info] supervisor_tool_called
  tool=query_documents firm_id=...``. The dev-loop default.
* ``json`` — :class:`structlog.processors.JSONRenderer`. CloudWatch and
  Sentry parse this directly. The production default.

``auto`` picks json when ``ENV=production`` and console otherwise. The
default behaviour the team wants 95% of the time. ``LOG_FORMAT=console``
forces console even in production for one-off debugging windows;
``LOG_FORMAT=json`` forces JSON locally to mirror prod parsing.
"""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.types import EventDict, Processor

from app.config import Environment, LogFormat, LogLevel, settings


def _add_service(_logger: object, _method: str, event_dict: EventDict) -> EventDict:
    event_dict.setdefault("service", "enma-backend")
    return event_dict


def _level_to_int(level: LogLevel) -> int:
    return {
        LogLevel.DEBUG: logging.DEBUG,
        LogLevel.INFO: logging.INFO,
        LogLevel.WARNING: logging.WARNING,
        LogLevel.ERROR: logging.ERROR,
    }[level]


def _pick_renderer() -> Processor:
    """Return the structlog renderer matching ``settings.log_format``.

    ``auto`` picks json in production, console otherwise — what the team
    wants by default. ``LOG_FORMAT`` is an explicit override surfaced
    via :class:`app.config.LogFormat`.
    """
    fmt = settings.log_format
    if fmt is LogFormat.JSON:
        return structlog.processors.JSONRenderer()
    if fmt is LogFormat.CONSOLE:
        return structlog.dev.ConsoleRenderer(colors=False)
    # auto
    if settings.env is Environment.PRODUCTION:
        return structlog.processors.JSONRenderer()
    return structlog.dev.ConsoleRenderer(colors=False)


def configure_logging() -> None:
    """Idempotently install structlog with an env-appropriate renderer."""

    level = _level_to_int(settings.log_level)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        _add_service,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            _pick_renderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging through structlog so uvicorn / sqlalchemy /
    # third-party libs share the same JSON format.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    for noisy in ("uvicorn.access", "uvicorn.error", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(level)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]


__all__ = ["configure_logging", "get_logger"]
