"""PII / secret masking helpers.

Two surfaces:

1. ``mask_dict`` — generic redaction for arbitrary log payloads.
2. ``sentry_before_send`` — Sentry ``before_send`` hook that scrubs the
   event envelope (request data, breadcrumbs, extras) before transmission.

Keep the sensitive-key list tight: matching is substring-based, so a key like
``user_pan_history`` matches ``pan`` and gets masked. Adding broad words like
``id`` would scrub legitimate identifiers — don't.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

SENSITIVE_KEY_FRAGMENTS: frozenset[str] = frozenset(
    {
        # Auth & infra secrets
        "telegram_bot_token",
        "bot_token",
        "api_key",
        "apikey",
        "secret",
        "password",
        "passwd",
        "token",
        "authorization",
        "database_url",
        "dsn",
        "encryption_key",
        "hmac_secret",
        # Indian tax PII
        "pan",
        "gstin",
        "aadhaar",
        "bank_account",
        "account_number",
        "ifsc",
    }
)

_MASK = "***"
_HEAD_KEEP = 4


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)


def _mask_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        # Keep a short prefix so the operator can still distinguish entries
        # in logs without learning the secret.
        return f"{value[:_HEAD_KEEP]}{_MASK}" if len(value) > _HEAD_KEEP else _MASK
    if isinstance(value, bool | int | float):
        return _MASK
    return _MASK


def mask_dict(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deep copy of ``data`` with sensitive values redacted.

    Recurses into nested mappings and into sequences of mappings. Leaves
    non-string scalars alone unless their key is sensitive.
    """
    masked: dict[str, Any] = {}
    for key, value in data.items():
        if _is_sensitive(key):
            masked[key] = _mask_value(value)
            continue
        if isinstance(value, Mapping):
            masked[key] = mask_dict(value)
            continue
        if isinstance(value, list | tuple) and value and isinstance(value[0], Mapping):
            masked[key] = [mask_dict(item) for item in value]
            continue
        masked[key] = value
    return masked


def _scrub_breadcrumbs(breadcrumbs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    scrubbed: list[dict[str, Any]] = []
    for crumb in breadcrumbs:
        item = dict(crumb)
        data = item.get("data")
        if isinstance(data, Mapping):
            item["data"] = mask_dict(data)
        scrubbed.append(item)
    return scrubbed


def sentry_before_send(
    event: dict[str, Any],
    _hint: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Sentry ``before_send`` hook.

    Strips request bodies, masks sensitive headers/cookies/extras, and redacts
    breadcrumb payloads. Returning ``None`` would drop the event entirely; we
    always forward the scrubbed event so error visibility is preserved.
    """
    request = event.get("request")
    if isinstance(request, dict):
        # Bodies can contain Telegram payloads (file_id, chat_id) — drop wholesale.
        if "data" in request:
            request["data"] = "[REDACTED]"
        for field in ("headers", "cookies", "env"):
            if isinstance(request.get(field), Mapping):
                request[field] = mask_dict(request[field])

    if isinstance(event.get("extra"), Mapping):
        event["extra"] = mask_dict(event["extra"])

    if isinstance(event.get("tags"), Mapping):
        event["tags"] = mask_dict(event["tags"])

    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, Mapping) and isinstance(breadcrumbs.get("values"), list):
        breadcrumbs["values"] = _scrub_breadcrumbs(breadcrumbs["values"])

    # PII goes through the user object — drop everything except a stable id hash.
    if isinstance(event.get("user"), Mapping):
        user = event["user"]
        event["user"] = {"id": user.get("id")} if "id" in user else {}

    return event


__all__ = ["SENSITIVE_KEY_FRAGMENTS", "mask_dict", "sentry_before_send"]
