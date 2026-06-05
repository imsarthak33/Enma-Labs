"""Health and readiness endpoints.

Two probes:

* ``/health``  — liveness. Cheap: returns metadata if the process is up.
* ``/ready``   — readiness. Confirms the DB is reachable (SELECT 1).

Kubernetes / Railway / Render conventions: liveness must never depend on
downstream services (a failing DB should NOT restart the pod), readiness
should reflect ability to serve traffic.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.config import settings
from app.db.session import get_session

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Liveness response payload."""

    status: Literal["ok"] = Field(default="ok")
    service: Literal["enma-backend"] = Field(default="enma-backend")
    version: str = Field(default=__version__)
    env: str


class ReadyResponse(BaseModel):
    """Readiness response payload."""

    status: Literal["ready", "degraded"]
    service: Literal["enma-backend"] = Field(default="enma-backend")
    database: Literal["ok", "fail"]
    detail: str | None = None


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe — process is up.",
    status_code=status.HTTP_200_OK,
)
async def health() -> HealthResponse:
    return HealthResponse(env=settings.env.value)


@router.get(
    "/ready",
    response_model=ReadyResponse,
    summary="Readiness probe — process can serve traffic.",
    status_code=status.HTTP_200_OK,
    responses={
        503: {
            "model": ReadyResponse,
            "description": "Dependency unreachable.",
        }
    },
)
async def ready(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReadyResponse:
    try:
        result = await session.execute(text("SELECT 1"))
        if result.scalar_one() != 1:
            raise RuntimeError("unexpected SELECT 1 result")
    except (SQLAlchemyError, RuntimeError) as exc:
        # We don't raise an HTTPException because we want the structured
        # ReadyResponse body in the failure case too. FastAPI will use the
        # default 200; that's wrong — flip to 503 via a starlette Response.
        from fastapi.responses import JSONResponse

        body = ReadyResponse(
            status="degraded", database="fail", detail=str(exc)
        ).model_dump()
        return JSONResponse(  # type: ignore[return-value]
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=body,
        )
    return ReadyResponse(status="ready", database="ok")
