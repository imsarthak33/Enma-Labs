"""Tests for the slash-command parser.

The dispatcher itself is exercised by integration tests against the
sqlite fixture (see ``tests/test_db/test_phase6_queries.py``); this
module focuses on the pure parsing surface.
"""

from __future__ import annotations

from app.agents.commands import (
    is_slash_command,
    parse_command,
)


class TestIsSlashCommand:
    def test_starts_with_slash(self) -> None:
        assert is_slash_command("/list_clients") is True

    def test_just_slash_is_not(self) -> None:
        assert is_slash_command("/") is False

    def test_plain_text(self) -> None:
        assert is_slash_command("hello") is False


class TestParseCommand:
    def test_unknown_verb_returns_none(self) -> None:
        assert parse_command("/explode") is None

    def test_list_clients_no_args(self) -> None:
        cmd = parse_command("/list_clients")
        assert cmd is not None
        assert cmd.verb == "list_clients"
        assert cmd.args == ()

    def test_add_client_with_quoted_name(self) -> None:
        cmd = parse_command('/add_client "ABC Industries Pvt Ltd" 27AABCU9603R1ZP')
        assert cmd is not None
        assert cmd.verb == "add_client"
        assert cmd.args == ("ABC Industries Pvt Ltd", "27AABCU9603R1ZP")

    def test_add_client_without_gstin(self) -> None:
        cmd = parse_command('/add_client "Single Co"')
        assert cmd is not None
        assert cmd.args == ("Single Co",)

    def test_verb_is_case_insensitive_via_lower(self) -> None:
        cmd = parse_command("/LIST_CLIENTS")
        assert cmd is not None
        assert cmd.verb == "list_clients"

    def test_unterminated_quote_returns_none(self) -> None:
        # shlex raises ValueError on unbalanced quotes.
        assert parse_command('/add_client "broken') is None

    def test_status_uses_remainder_for_name(self) -> None:
        cmd = parse_command("/status ABC Industries")
        assert cmd is not None
        assert cmd.verb == "status"
        assert cmd.args == ("ABC", "Industries")

    def test_empty_after_slash_returns_none(self) -> None:
        assert parse_command("/  ") is None
