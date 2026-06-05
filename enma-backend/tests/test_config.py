"""Config validation tests — boots fast-fail behaviour for misconfig."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_database_url_rejects_sync_driver() -> None:
    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql://enma:dev_password@localhost:5432/enma_dev",
        )


def test_cors_wildcard_rejected_outside_development() -> None:
    from app.config import Environment, Settings

    with pytest.raises(ValueError, match="cors_origins"):
        Settings(env=Environment.PRODUCTION, cors_origins=["*"])


def test_settings_safe_repr_masks_secrets() -> None:
    from app.config import Settings

    s = Settings()
    snap = s.safe_repr()
    assert snap["telegram_bot_token"] == "***"
    assert snap["backend_api_key"] == "***"
    assert snap["gateway_hmac_secret"] == "***"
