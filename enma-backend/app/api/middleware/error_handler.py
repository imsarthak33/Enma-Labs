"""Global exception handler.

Catches anything not handled by FastAPI/Starlette, logs it with the bound
request context, optionally reports to Sentry, and returns a clean JSON
error envelope. Never leaks tracebacks to clients.

IMPORTANT: We register handlers for BOTH ``starlette.exceptions.HTTPException``
(catches Starlette-internal 404/405 for unmatched routes) AND
``fastapi.HTTPException`` (catches application-raised HTTP errors). Without
the Starlette handler, 404 and 405 bypass our envelope and return the
default ``{"detail": "Not Found"}`` format.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging_setup import get_logger

_log = get_logger(__name__)


def _envelope(*, code: str, message: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return body


def register_exception_handlers(app: FastAPI) -> None:
    # Catch ALL HTTP exceptions — including Starlette's internal 404/405
    # that fire for unmatched routes and disallowed methods.
    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(_req: Request, exc: StarletteHTTPException) -> JSONResponse:
        _log.warning(
            "http_exception",
            status_code=exc.status_code,
            detail=str(exc.detail),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(
                code=f"http_{exc.status_code}",
                message=str(exc.detail),
            ),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(_req: Request, exc: RequestValidationError) -> JSONResponse:
        _log.info("validation_error", errors=exc.errors())
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_envelope(
                code="validation_error",
                message="Request payload failed validation.",
                details=exc.errors(),
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, exc: Exception) -> JSONResponse:
        # structlog renders exc_info via the format_exc_info processor.
        _log.error("unhandled_exception", error=str(exc), exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope(
                code="internal_error",
                message="An internal error occurred.",
            ),
        )


__all__ = ["register_exception_handlers"]
