"""Tests for PII / secret masking utilities.

Coverage targets:
    * Substring matching catches nested-key variants.
    * Recursive masking handles dicts and lists-of-dicts.
    * ``sentry_before_send`` redacts request bodies, headers, extras,
      breadcrumb data, and user objects.
"""

from __future__ import annotations

from app.utils.masking import mask_dict, sentry_before_send


class TestMaskDict:
    def test_top_level_secret_masked(self) -> None:
        result = mask_dict({"telegram_bot_token": "1234567890:ABCDEF", "ok": "yes"})
        assert result["telegram_bot_token"].startswith("1234")
        assert "ABCDEF" not in result["telegram_bot_token"]
        assert result["ok"] == "yes"

    def test_substring_match_catches_nested_keys(self) -> None:
        result = mask_dict({"user_pan_history": "ABCDE1234F"})
        assert "ABCDE1234F" not in str(result["user_pan_history"])

    def test_short_value_fully_masked(self) -> None:
        # Strings shorter than the prefix window must be fully redacted.
        assert mask_dict({"api_key": "ab"})["api_key"] == "***"

    def test_none_value_left_as_none(self) -> None:
        assert mask_dict({"api_key": None})["api_key"] is None

    def test_recurses_into_nested_dicts(self) -> None:
        payload = {"upstream": {"backend": {"hmac_secret": "topsecret-value"}}}
        masked = mask_dict(payload)
        assert "topsecret-value" not in str(masked)

    def test_recurses_into_list_of_dicts(self) -> None:
        payload = {"clients": [{"gstin": "07AAAAA0000A1Z5"}, {"gstin": "07BBBBB0000B1Z5"}]}
        masked = mask_dict(payload)
        for client in masked["clients"]:
            assert "0000" not in client["gstin"]

    def test_non_sensitive_lists_passthrough(self) -> None:
        payload = {"tags": ["a", "b", "c"]}
        assert mask_dict(payload)["tags"] == ["a", "b", "c"]

    def test_int_secret_value_redacted(self) -> None:
        # Non-string secret values still get redacted.
        assert mask_dict({"password": 12345})["password"] == "***"


class TestSentryBeforeSend:
    def test_request_body_redacted(self) -> None:
        event: dict[str, object] = {
            "request": {"data": {"chat_id": 42, "file_id": "ABC"}},
        }
        scrubbed = sentry_before_send(event)
        assert scrubbed is not None
        assert scrubbed["request"]["data"] == "[REDACTED]"

    def test_request_headers_masked(self) -> None:
        event: dict[str, object] = {
            "request": {"headers": {"authorization": "Bearer secrettoken"}},
        }
        scrubbed = sentry_before_send(event)
        assert scrubbed is not None
        assert "secrettoken" not in str(scrubbed["request"]["headers"])

    def test_extras_masked(self) -> None:
        event: dict[str, object] = {
            "extra": {"telegram_bot_token": "12345:ABCDEF", "request_id": "req-1"},
        }
        scrubbed = sentry_before_send(event)
        assert scrubbed is not None
        assert "ABCDEF" not in str(scrubbed["extra"])
        assert scrubbed["extra"]["request_id"] == "req-1"

    def test_breadcrumb_data_masked(self) -> None:
        event: dict[str, object] = {
            "breadcrumbs": {
                "values": [
                    {"category": "http", "data": {"api_key": "supersecretkey"}},
                    {"category": "log", "message": "hi"},
                ]
            },
        }
        scrubbed = sentry_before_send(event)
        assert scrubbed is not None
        breadcrumbs = scrubbed["breadcrumbs"]
        assert isinstance(breadcrumbs, dict)
        values = breadcrumbs["values"]
        assert "supersecretkey" not in str(values[0]["data"])

    def test_user_object_stripped_to_id(self) -> None:
        event: dict[str, object] = {
            "user": {"id": "user-1", "email": "a@b.co", "ip_address": "1.2.3.4"},
        }
        scrubbed = sentry_before_send(event)
        assert scrubbed is not None
        assert scrubbed["user"] == {"id": "user-1"}

    def test_event_without_pii_passes_through(self) -> None:
        event: dict[str, object] = {"message": "hello"}
        scrubbed = sentry_before_send(event)
        assert scrubbed == {"message": "hello"}
