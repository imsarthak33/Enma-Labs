"""Morning briefing — pure-Python HTML digest builder.

Pulled together at 09:00 IST Mon–Sat. No LLM call — the briefing is
structured aggregation. Pieces:

  1. **Greeting** with the firm name + IST date.
  2. **Pending tasks** — count + the top 5 by due date.
  3. **Overdue tasks** — separate section so they jump out.
  4. **Open filings** — current + upcoming period, document counts.
  5. **News** — up to 3 Serper-sourced links (already capped + cached).

The renderer is split into small functions per section so each is
unit-testable in isolation. The top-level :func:`build_briefing_html`
just concatenates the sections it was handed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.db.models.task import Task
from app.formatting.telegram_html import bold, code, italic, link, safe_text
from app.services.scraper import NewsItem
from app.utils.date_utils import FilingPeriod

__all__ = [
    "BriefingData",
    "build_briefing_html",
    "render_news_section",
    "render_tasks_section",
]


@dataclass(frozen=True)
class BriefingData:
    """Everything the renderer needs. Built by the cron route."""

    firm_name: str
    as_of_ist: datetime
    open_tasks: Sequence[Task]
    overdue_tasks: Sequence[Task]
    current_period: FilingPeriod
    upcoming_period: FilingPeriod
    current_period_doc_count: int
    upcoming_period_doc_count: int
    news: tuple[NewsItem, ...]


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


def _render_header(data: BriefingData) -> str:
    date_str = data.as_of_ist.strftime("%a %d %b %Y")
    return (
        bold("Morning brief — " + safe_text(data.firm_name))
        + "\n"
        + italic(date_str + " IST")
    )


def render_tasks_section(data: BriefingData) -> str:
    """Pending + overdue tasks. Overdue first so they jump out."""
    parts: list[str] = []
    if data.overdue_tasks:
        parts.append(bold(f"Overdue tasks ({len(data.overdue_tasks)})"))
        for t in data.overdue_tasks[:5]:
            parts.append("• " + safe_text(t.title))
        if len(data.overdue_tasks) > 5:
            parts.append(italic(f"+ {len(data.overdue_tasks) - 5} more"))
    parts.append("")  # blank line separator
    parts.append(bold(f"Pending tasks ({len(data.open_tasks)})"))
    if not data.open_tasks:
        parts.append(italic("None open."))
    else:
        for t in data.open_tasks[:5]:
            due = (
                " — due " + safe_text(t.due_at.strftime("%d-%b-%Y"))
                if t.due_at is not None
                else ""
            )
            parts.append("• " + safe_text(t.title) + due)
        if len(data.open_tasks) > 5:
            parts.append(italic(f"+ {len(data.open_tasks) - 5} more"))
    return "\n".join(parts)


def render_filings_section(data: BriefingData) -> str:
    return "\n".join(
        [
            bold("Filings"),
            (
                "Current "
                + code(f"{data.current_period.month:02d}/{data.current_period.year}")
                + f" — {data.current_period_doc_count} document(s)"
            ),
            (
                "Upcoming "
                + code(f"{data.upcoming_period.month:02d}/{data.upcoming_period.year}")
                + f" — {data.upcoming_period_doc_count} document(s)"
            ),
        ]
    )


def render_news_section(data: BriefingData) -> str:
    if not data.news:
        return bold("Compliance news") + "\n" + italic("No fresh items today.")
    lines: list[str] = [bold("Compliance news")]
    for item in data.news[:3]:
        title = link(item.link, item.title)
        meta_parts: list[str] = []
        if item.source:
            meta_parts.append(safe_text(item.source))
        if item.published_at:
            meta_parts.append(safe_text(item.published_at))
        meta = " · ".join(meta_parts)
        lines.append("• " + title + (f" — {italic(meta)}" if meta else ""))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Top-level renderer
# ---------------------------------------------------------------------------


def build_briefing_html(data: BriefingData) -> str:
    """Compose the full HTML digest from its sections."""
    return "\n\n".join(
        [
            _render_header(data),
            render_tasks_section(data),
            render_filings_section(data),
            render_news_section(data),
        ]
    )
