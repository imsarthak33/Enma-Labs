"""FastAPI application factory and process entrypoint.

Run with:  ``uvicorn app.main:app --host 0.0.0.0 --port 8000``
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from app import __version__
from app.api.middleware.error_handler import register_exception_handlers
from app.api.middleware.rate_limit import attach_rate_limiter
from app.api.middleware.request_id import RequestIDMiddleware
from app.api.router import api_router
from app.config import settings
from app.db.session import dispose_engine, get_engine
from app.logging_setup import configure_logging, get_logger
from app.services.cache import close_redis, get_redis
from app.services.telegram import close_client as close_telegram_client
from app.utils.background import shutdown_registry
from app.utils.masking import sentry_before_send


def _init_sentry() -> None:
    if not settings.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        environment=settings.env.value,
        release=__version__,
        send_default_pii=False,
        before_send=sentry_before_send,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            StarletteIntegration(transaction_style="endpoint"),
        ],
    )


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Process lifecycle: configure logging, init Sentry, warm DB pool, dispose on exit."""
    configure_logging()
    _init_sentry()
    log = get_logger(__name__)
    log.info("app_startup", settings=settings.safe_repr())

    # Eagerly construct the engine so any DSN problem surfaces at boot,
    # not on the first request.
    get_engine()
    # Warm the Redis client too — surfaces DSN typos before the first hit.
    # ``get_redis`` returns None when REDIS_URL is unset, which is fine.
    get_redis()

    try:
        yield
    finally:
        log.info("app_shutdown")
        # Drain background tasks first so they get a chance to complete
        # any pending DB writes BEFORE we tear the engine down.
        await shutdown_registry(timeout=30.0)
        await close_telegram_client()
        await close_redis()
        await dispose_engine()


def create_app() -> FastAPI:
    """Application factory — explicit so tests can construct fresh instances."""
    app = FastAPI(
        title="Enma Labs Backend",
        version=__version__,
        description="Autonomous Chief of Staff for Indian CA firms.",
        openapi_url="/openapi.json" if not settings.is_production() else None,
        docs_url="/docs" if not settings.is_production() else None,
        redoc_url="/redoc" if not settings.is_production() else None,
        default_response_class=__import__(
            "fastapi.responses", fromlist=["ORJSONResponse"]
        ).ORJSONResponse,
        lifespan=lifespan,
    )

    # ---- Middleware (order matters: outermost added last in Starlette) -----
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    # ---- Rate limiting -----------------------------------------------------
    # Attaches the slowapi Limiter to ``app.state`` and registers the 429
    # exception handler. Must run before ``include_router`` so the limiter
    # decorators inside route modules see the live Limiter instance.
    attach_rate_limiter(app)

    # ---- Error handlers ----------------------------------------------------
    register_exception_handlers(app)

    # ---- Routes ------------------------------------------------------------
    app.include_router(api_router)

    return app


# uvicorn / gunicorn import target.
app: FastAPI = create_app()


__all__ = ["app", "create_app", "lifespan"]
