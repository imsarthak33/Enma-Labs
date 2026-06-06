"""Tests for the morning-briefing HTML renderer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.agents.briefing import (
    BriefingData,
    build_briefing_html,
    render_news_section,
    render_tasks_section,
)
from app.services.scraper import NewsItem
from app.utils.date_utils import FilingPeriod


def _task(title: str, *, due_in_days: int | None = None) -> SimpleNamespace:
    due = (
        datetime(2026, 6, 6, tzinfo=UTC) + timedelta(days=due_in_days)
        if due_in_days is not None
        else None
    )
    return SimpleNamespace(title=title, due_at=due)


def _data(**overrides: object) -> BriefingData:
    base = {
        "firm_name": "Test & Co",  # ampersand exercises HTML escape
        "as_of_ist": datetime(2026, 6, 6, 9, 0, tzinfo=UTC),
        "open_tasks": [],
        "overdue_tasks": [],
        "current_period": FilingPeriod(year=2026, month=6),
        "upcoming_period": FilingPeriod(year=2026, month=7),
        "current_period_doc_count": 0,
        "upcoming_period_doc_count": 0,
        "news": (),
    }
    base.update(overrides)
    return BriefingData(**base)  # type: ignore[arg-type]


class TestTasksSection:
    def test_no_tasks(self) -> None:
        html = render_tasks_section(_data())
        assert "Pending tasks (0)" in html
        assert "None open." in html
        assert "Overdue" not in html

    def test_overdue_listed_first(self) -> None:
        html = render_tasks_section(
            _data(
                overdue_tasks=[_task("A overdue")],
                open_tasks=[_task("B pending", due_in_days=3)],
            )
        )
        assert html.index("Overdue") < html.index("Pending tasks")
        assert "A overdue" in html
        assert "B pending" in html

    def test_tail_count(self) -> None:
        many = [_task(f"task {i}") for i in range(8)]
        html = render_tasks_section(_data(open_tasks=many))
        assert "+ 3 more" in html


class TestNewsSection:
    def test_empty_news(self) -> None:
        html = render_news_section(_data())
        assert "No fresh items today" in html

    def test_three_links(self) -> None:
        news = (
            NewsItem(
                title="GST notice",
                link="https://example.com/1",
                snippet="",
                source="CBIC",
                published_at="2h ago",
            ),
            NewsItem(
                title="Income tax circular",
                link="https://example.com/2",
                snippet="",
                source=None,
                published_at=None,
            ),
            NewsItem(
                title="Section 16 update",
                link="https://example.com/3",
                snippet="",
                source="ET",
                published_at="1d ago",
            ),
        )
        html = render_news_section(_data(news=news))
        assert 'href="https://example.com/1"' in html
        assert "GST notice" in html
        assert "CBIC" in html


class TestFullBriefing:
    def test_firm_name_escaped(self) -> None:
        html = build_briefing_html(_data())
        # "Test & Co" must become "Test &amp; Co" in the bold header.
        assert "Test &amp; Co" in html
        assert "<b>Morning brief" in html

    def test_filings_section_included(self) -> None:
        html = build_briefing_html(
            _data(
                current_period_doc_count=12,
                upcoming_period_doc_count=3,
            )
        )
        assert "Filings" in html
        assert "12 document(s)" in html
        assert "3 document(s)" in html
