"""Agentic-trajectory CRUD — append-only, firm-scoped.

The trajectory table is the LoRA training corpus's behavioural layer:
every supervisor turn the firm has with Enma is captured here. Only
two write paths exist (``record_turn``) and one read path
(``list_recent_for_chat`` — used by P3 brain surface, not by P0
runtime code). No update or delete; the corpus is append-only.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from app.db.models.trajectory import AgenticTrajectory
from app.db.queries.base import BaseQuery


class TrajectoryQuery(BaseQuery):
    """Tenant-scoped trajectory operations."""

    async def record_turn(
        self,
        *,
        chat_id: int | None,
        user_text: str,
        tool_calls_made: list[dict[str, Any]],
        final_reply: str | None,
        input_tokens: int,
        output_tokens: int,
        llm_calls: list[dict[str, Any]] | None = None,
    ) -> AgenticTrajectory:
        """Append one supervisor turn to the trajectory log.

        Never raises on partial input — token counts default to 0,
        ``llm_calls`` is optional. The supervisor caller wraps this in
        a best-effort ``try / except`` so a logging failure cannot
        break a user-facing reply.
        """
        row = AgenticTrajectory(
            chat_id=chat_id,
            user_text=user_text,
            tool_calls_made=tool_calls_made,
            final_reply=final_reply,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            llm_calls=llm_calls or [],
        )
        return cast(AgenticTrajectory, await self._insert(row))

    async def list_recent_for_chat(
        self, *, chat_id: int, limit: int = 50
    ) -> Sequence[AgenticTrajectory]:
        """Most-recent-first trajectory rows for one chat (firm-scoped)."""
        stmt = (
            self._scoped_select(AgenticTrajectory)
            .where(AgenticTrajectory.chat_id == chat_id)
            .order_by(AgenticTrajectory.created_at.desc())
            .limit(limit)
        )
        return await self._fetch_all(stmt)


__all__ = ["TrajectoryQuery"]
