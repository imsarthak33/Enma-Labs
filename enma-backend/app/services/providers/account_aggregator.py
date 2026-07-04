"""Account Aggregator adapter (Track-A automation, Phase 7c) — ADR-018.

Zero-touch bank data via the RBI/Sahamati Account Aggregator framework. As a
registered FIU (Financial Information User), Enma requests the taxpayer's
consent once; thereafter the AA delivers the client's bank Financial
Information (FI) on a schedule, replacing the manual statement upload / IMAP
poll entirely.

Two-step, per the AA spec (both are INTEGRATION POINTs — the exact request/
response shapes are set by the chosen AA gateway, e.g. Finvu / OneMoney / a
TSP aggregating them):

    1. :meth:`request_consent` — kick off the customer's one-time consent (the
       taxpayer approves in their AA app), yielding a durable ``consent_id``.
    2. :meth:`fetch_transactions` — pull the FI for a date range and hand back
       normalised statement bytes the existing bank-ingest seam already reads
       (``auto_ingest.ingest_bank_statement_bytes``), so nothing downstream of
       ingestion changes vs an upload.

Dormant by default: with no ``AA_BASE_URL`` + ``AA_API_KEY`` in the env,
:func:`get_account_aggregator_client` returns :class:`NullAccountAggregatorClient`
and nothing fires. Paste the FIU credentials into the env to activate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final, Protocol, runtime_checkable

import httpx

from app.config import Settings
from app.logging_setup import get_logger

__all__ = [
    "AaConsent",
    "AccountAggregatorProvider",
    "HttpAccountAggregatorClient",
    "NullAccountAggregatorClient",
    "get_account_aggregator_client",
]

_log = get_logger(__name__)

_REQUEST_TIMEOUT_S: Final[float] = 30.0


@dataclass(frozen=True)
class AaConsent:
    """A durable AA consent handle for one client's bank FI."""

    consent_id: str


@runtime_checkable
class AccountAggregatorProvider(Protocol):
    """Requests consent + pulls a client's bank FI from an Account Aggregator."""

    @property
    def is_configured(self) -> bool: ...

    async def request_consent(self, *, client_ref: str) -> AaConsent | None:
        """Start the customer's one-time AA consent; return a durable handle."""
        ...

    async def fetch_transactions(
        self, *, consent_id: str, from_date: date, to_date: date
    ) -> bytes | None:
        """Pull FI for the range as normalised statement bytes, or ``None``."""
        ...


class NullAccountAggregatorClient:
    """No-op adapter used when no AA gateway is configured (the default)."""

    @property
    def is_configured(self) -> bool:
        return False

    async def request_consent(self, *, client_ref: str) -> AaConsent | None:
        return None

    async def fetch_transactions(
        self, *, consent_id: str, from_date: date, to_date: date
    ) -> bytes | None:
        return None


class HttpAccountAggregatorClient:
    """Real AA/FIU adapter — active only when base URL + API key are present."""

    def __init__(self, *, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return True

    async def request_consent(self, *, client_ref: str) -> AaConsent | None:
        """INTEGRATION POINT — the consent-request payload (purpose code, FI
        types, fetch frequency, data range) is AA-spec-driven. The generic
        shape below posts a client reference and reads a ``consent_id`` back.
        """
        url = f"{self._base_url}/consents"
        headers = {"x-api-key": self._api_key, "accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
                resp = await client.post(url, json={"client_ref": client_ref}, headers=headers)
        except httpx.HTTPError as exc:
            _log.error("aa_consent_request_failed", client_ref=client_ref, error=str(exc))
            return None
        if resp.status_code != httpx.codes.OK:
            _log.warning("aa_consent_request_non_200", status=resp.status_code)
            return None
        cid = str((resp.json() or {}).get("consent_id") or "").strip()
        return AaConsent(consent_id=cid) if cid else None

    async def fetch_transactions(
        self, *, consent_id: str, from_date: date, to_date: date
    ) -> bytes | None:
        """INTEGRATION POINT — the FI-fetch call + the FI schema→statement
        normalisation are AA-spec-driven. Returns bytes the bank-ingest seam
        parses; the caller feeds them to ``auto_ingest`` unchanged.
        """
        url = f"{self._base_url}/fi/fetch"
        headers = {"x-api-key": self._api_key, "accept": "application/json"}
        params = {
            "consent_id": consent_id,
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
        }
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
                resp = await client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            _log.error("aa_fi_fetch_failed", consent_id=consent_id, error=str(exc))
            return None
        if resp.status_code != httpx.codes.OK:
            _log.warning("aa_fi_fetch_non_200", status=resp.status_code)
            return None
        return resp.content


def get_account_aggregator_client(settings: Settings) -> AccountAggregatorProvider:
    """Return the real AA client when configured, else the no-op client."""
    if not settings.is_account_aggregator_configured():
        return NullAccountAggregatorClient()
    assert settings.aa_base_url is not None
    assert settings.aa_api_key is not None
    _log.info("account_aggregator_client_active", base_url=settings.aa_base_url)
    return HttpAccountAggregatorClient(
        base_url=settings.aa_base_url,
        api_key=settings.aa_api_key.get_secret_value(),
    )
