"""Tests for the Telegram HTML formatter.

We don't test Telegram itself — we test that every text segment is escaped
and every link enforces the scheme allowlist.
"""

from __future__ import annotations

import pytest
from app.formatting.telegram_html import bold, code, italic, link, pre, safe_text


class TestSafeText:
    def test_escapes_ampersand_lt_gt(self) -> None:
        assert safe_text("a & b < c > d") == "a &amp; b &lt; c &gt; d"

    def test_escapes_double_quote(self) -> None:
        # quote=True path — important for href attributes.
        assert safe_text('a "b" c') == "a &quot;b&quot; c"

    def test_none_is_empty(self) -> None:
        assert safe_text(None) == ""

    def test_coerces_non_strings(self) -> None:
        assert safe_text(42) == "42"
        # A Decimal-shaped object should serialise via str().
        from decimal import Decimal
        assert safe_text(Decimal("1234.56")) == "1234.56"


class TestInlineEntities:
    def test_bold_wraps_and_escapes(self) -> None:
        assert bold("hi <there>") == "<b>hi &lt;there&gt;</b>"

    def test_italic_wraps_and_escapes(self) -> None:
        assert italic("a & b") == "<i>a &amp; b</i>"

    def test_code_wraps_and_escapes(self) -> None:
        assert code("29ABCDE1234F1Z5") == "<code>29ABCDE1234F1Z5</code>"

    def test_pre_wraps_and_escapes(self) -> None:
        assert pre("line1\nline2 & line3") == "<pre>line1\nline2 &amp; line3</pre>"


class TestLink:
    def test_https_link_escapes_text(self) -> None:
        result = link("https://example.com/x?y=1", "click <here>")
        assert result == '<a href="https://example.com/x?y=1">click &lt;here&gt;</a>'

    def test_tg_link_allowed(self) -> None:
        assert link("tg://user?id=42", "user") == '<a href="tg://user?id=42">user</a>'

    @pytest.mark.parametrize(
        "bad_url",
        [
            "http://insecure.example.com",
            "javascript:alert(1)",
            "ftp://example.com",
            "",
        ],
    )
    def test_rejects_disallowed_schemes(self, bad_url: str) -> None:
        with pytest.raises(ValueError):
            link(bad_url, "x")

    def test_rejects_non_string_url(self) -> None:
        with pytest.raises(ValueError):
            link(None, "x")  # type: ignore[arg-type]

    def test_quoted_chars_in_url_are_escaped(self) -> None:
        # Double-quote in the URL must not break out of the href attribute.
        result = link('https://example.com/?q="x"', "ok")
        assert '"' not in result.replace('href="', "").split(">")[0][:-1]
        assert "&quot;" in result
