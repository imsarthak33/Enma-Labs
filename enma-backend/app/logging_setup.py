"""Structured JSON logging via structlog.

Single entrypoint: ``configure_logging()`` is called once during FastAPI
lifespan startup. After that, ``structlog.get_logger(__name__)`` returns
loggers that emit JSON to stdout with consistent timestamps and contextual
binds (request_id, ca_firm_id, etc., set later via middleware).
"""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.types import EventDict, Processor

from app.config import LogLevel, settings


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


def configure_logging() -> None:
    """Idempotently install JSON logging on the root logger + structlog."""

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
            structlog.processors.JSONRenderer(),
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
