"""Tests — Phase 7c dormant provider adapters (AA + Tally connector).

Verify the env-gated factory pattern: unconfigured → Null (no-op), configured
→ the real client. No network is exercised.
"""

from __future__ import annotations

from datetime import date

from app.services.providers.account_aggregator import (
    HttpAccountAggregatorClient,
    NullAccountAggregatorClient,
    get_account_aggregator_client,
)
from app.services.providers.tally_connector import (
    HttpTallyConnectorClient,
    NullTallyConnectorClient,
    _day_book_request_xml,
    get_tally_connector_client,
)


class _Settings:
    """Minimal settings stand-in exposing only the gate methods + fields."""

    def __init__(self, **kw: object) -> None:
        self.aa_base_url = kw.get("aa_base_url")
        self.aa_api_key = kw.get("aa_api_key")
        self.tally_connector_url = kw.get("tally_connector_url")
        self.tally_connector_api_key = kw.get("tally_connector_api_key")

    def is_account_aggregator_configured(self) -> bool:
        return bool(self.aa_base_url and self.aa_api_key)

    def is_tally_connector_configured(self) -> bool:
        return bool(self.tally_connector_url)


class _Secret:
    def __init__(self, v: str) -> None:
        self._v = v

    def get_secret_value(self) -> str:
        return self._v


# ---- Account Aggregator ----------------------------------------------------


def test_aa_dormant_when_unconfigured() -> None:
    client = get_account_aggregator_client(_Settings())
    assert isinstance(client, NullAccountAggregatorClient)
    assert client.is_configured is False


async def test_aa_null_client_no_ops() -> None:
    client = NullAccountAggregatorClient()
    assert await client.request_consent(client_ref="c1") is None
    assert (
        await client.fetch_transactions(
            consent_id="x", from_date=date(2026, 3, 1), to_date=date(2026, 3, 31)
        )
        is None
    )


def test_aa_active_when_configured() -> None:
    client = get_account_aggregator_client(
        _Settings(aa_base_url="https://aa.example", aa_api_key=_Secret("k"))
    )
    assert isinstance(client, HttpAccountAggregatorClient)
    assert client.is_configured is True


# ---- Tally connector -------------------------------------------------------


def test_tally_dormant_when_unconfigured() -> None:
    client = get_tally_connector_client(_Settings())
    assert isinstance(client, NullTallyConnectorClient)
    assert client.is_configured is False


async def test_tally_null_client_no_ops() -> None:
    client = NullTallyConnectorClient()
    assert (
        await client.fetch_day_book(
            company="S.S Traders", from_date=date(2026, 3, 1), to_date=date(2026, 3, 31)
        )
        is None
    )


def test_tally_active_when_url_set() -> None:
    client = get_tally_connector_client(
        _Settings(tally_connector_url="http://localhost:9000")
    )
    assert isinstance(client, HttpTallyConnectorClient)
    assert client.is_configured is True


def test_tally_day_book_request_xml_shape() -> None:
    xml = _day_book_request_xml(
        company="S.S Traders", from_date=date(2026, 3, 1), to_date=date(2026, 3, 31)
    )
    assert "<REPORTNAME>Day Book</REPORTNAME>" in xml
    assert "<SVCURRENTCOMPANY>S.S Traders</SVCURRENTCOMPANY>" in xml
    assert "20260301" in xml and "20260331" in xml
