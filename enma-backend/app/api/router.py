"""Top-level router aggregation.

Each phase mounts its routes here as they land:
    Phase 0: health
    Phase 3: worker
    Phase 6: admin
    Phase 7: cron
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import health, worker

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(worker.router)

__all__ = ["api_router"]
