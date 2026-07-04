"""GSTR-2B-via-GSP adapter (Track-A automation, Phase 7b).

The second provider on the env-gated pattern (see
:mod:`app.services.providers.base`). A **GST Suvidha Provider** exposes the
GSTN APIs; this adapter pulls a client's GSTR-2B JSON for a return period and
hands the bytes to the same ingestion path an upload takes (sniff →
``brain_events(source='gstn_portal')`` → auto-reconcile).

Dormant by default: with no ``GSP_BASE_URL`` + ``GSP_API_KEY`` in the env,
:func:`get_gsp_client` returns :class:`NullGspClient` and nothing fires.
Paste the GSP credentials into the env and :class:`HttpGspClient` activates.

Two provider-specific things to finalise when a GSP is chosen (the call shape
varies across ClearTax / Masters India / etc.) — both are marked INTEGRATION
POINT below:

1. the exact request path + query params + auth-header names, and
2. **per-client OTP consent:** GSTN requires the taxpayer to authorise the
   GSP via OTP, yielding an auth token (valid ~6h, refreshable ~30 days). The
   ``auth_token`` is threaded through :meth:`fetch_gstr2b`; storing/refreshing
   it per consented client is a separate (Phase 7b.2) concern.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol, runtime_checkable

import httpx

from app.config import Settings
from app.logging_setup import get_logger

__all__ = [
    "FilingReceipt",
    "GspAuthToken",
    "GspProvider",
    "HttpGspClient",
    "NullGspClient",
    "OtpChallenge",
    "get_gsp_client",
]

_log = get_logger(__name__)

_REQUEST_TIMEOUT_S: Final[float] = 30.0
# GSTN OTP-consent tokens are short-lived and refreshable. Absent a real
# expiry from the GSP response, assume this conservative window so the pull
# cron re-consents rather than calling with a stale token.
_DEFAULT_TOKEN_TTL: Final[timedelta] = timedelta(hours=6)


@dataclass(frozen=True)
class OtpChallenge:
    """Opaque handle correlating an OTP request to its later verification."""

    txn_id: str


@dataclass(frozen=True)
class GspAuthToken:
    """A per-client GSP auth token (from OTP consent) plus its expiry."""

    token: str
    expires_at: datetime


@dataclass(frozen=True)
class FilingReceipt:
    """The GSTN acknowledgement of a filed return (Phase 9 submission)."""

    reference_id: str  # ARN / acknowledgement number
    status: str


@runtime_checkable
class GspProvider(Protocol):
    """Pulls a client's GSTR-2B JSON + drives the per-client OTP consent."""

    @property
    def is_configured(self) -> bool: ...

    async def fetch_gstr2b(
        self, *, gstin: str, return_period: str, auth_token: str
    ) -> bytes | None:
        """Return the GSTR-2B JSON bytes, or ``None`` when unavailable."""
        ...

    async def request_consent_otp(self, *, gstin: str) -> OtpChallenge | None:
        """Ask the GSP to send a consent OTP to the taxpayer's registered mobile.

        Returns a challenge whose ``txn_id`` is presented back at verification,
        or ``None`` when the GSP is unconfigured / the request fails.
        """
        ...

    async def verify_consent_otp(
        self, *, gstin: str, txn_id: str, otp: str
    ) -> GspAuthToken | None:
        """Exchange the OTP for a per-client auth token, or ``None`` on failure."""
        ...

    async def file_return(
        self,
        *,
        gstin: str,
        return_type: str,
        return_period: str,
        auth_token: str,
        payload: dict[str, object],
    ) -> FilingReceipt | None:
        """Submit an APPROVED return (GSTR-1/3B) to GSTN via the GSP (Phase 9).

        Returns the GSTN acknowledgement, or ``None`` when unconfigured / the
        submission fails. Only ever called on a CA-approved filing snapshot.
        """
        ...


class NullGspClient:
    """No-op adapter used when no GSP is configured (the default)."""

    @property
    def is_configured(self) -> bool:
        return False

    async def fetch_gstr2b(
        self, *, gstin: str, return_period: str, auth_token: str
    ) -> bytes | None:
        return None

    async def request_consent_otp(self, *, gstin: str) -> OtpChallenge | None:
        return None

    async def verify_consent_otp(
        self, *, gstin: str, txn_id: str, otp: str
    ) -> GspAuthToken | None:
        return None

    async def file_return(
        self,
        *,
        gstin: str,
        return_type: str,
        return_period: str,
        auth_token: str,
        payload: dict[str, object],
    ) -> FilingReceipt | None:
        return None


class HttpGspClient:
    """Real GSP adapter — active only when base URL + API key are present."""

    def __init__(self, *, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return True

    async def fetch_gstr2b(
        self, *, gstin: str, return_period: str, auth_token: str
    ) -> bytes | None:
        """Pull one client's GSTR-2B JSON for ``return_period`` (MMYYYY).

        INTEGRATION POINT — the path, query params, and auth-header names are
        GSP-specific; the shape below is a sensible generic default. Adjust to
        the chosen GSP's spec when subscribing; the dormant/active gating and
        the downstream ingestion never change.
        """
        url = f"{self._base_url}/gstr2b"
        params = {"gstin": gstin, "rtnprd": return_period}
        headers = {
            "x-api-key": self._api_key,
            "x-auth-token": auth_token,  # per-client OTP-consent token
            "accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
                resp = await client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            _log.error("gsp_fetch_failed", gstin=gstin, period=return_period, error=str(exc))
            return None
        if resp.status_code != httpx.codes.OK:
            _log.warning(
                "gsp_fetch_non_200",
                gstin=gstin,
                period=return_period,
                status=resp.status_code,
            )
            return None
        return resp.content

    async def request_consent_otp(self, *, gstin: str) -> OtpChallenge | None:
        """Trigger the GSTN OTP to the taxpayer's registered mobile.

        INTEGRATION POINT — path + payload + the field holding the correlation
        id are GSP-specific. The generic shape below (POST a gstin, read a
        ``txn`` back) matches the common GSP surface; adjust on subscription.
        """
        url = f"{self._base_url}/consent/otp/request"
        headers = {"x-api-key": self._api_key, "accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
                resp = await client.post(url, json={"gstin": gstin}, headers=headers)
        except httpx.HTTPError as exc:
            _log.error("gsp_otp_request_failed", gstin=gstin, error=str(exc))
            return None
        if resp.status_code != httpx.codes.OK:
            _log.warning("gsp_otp_request_non_200", gstin=gstin, status=resp.status_code)
            return None
        txn = str((resp.json() or {}).get("txn") or "").strip()
        if not txn:
            _log.warning("gsp_otp_request_no_txn", gstin=gstin)
            return None
        return OtpChallenge(txn_id=txn)

    async def verify_consent_otp(
        self, *, gstin: str, txn_id: str, otp: str
    ) -> GspAuthToken | None:
        """Exchange (txn, otp) for a per-client auth token.

        INTEGRATION POINT — response field names (``auth_token`` / ``expiry``)
        vary by GSP. When the response carries no explicit expiry we fall back
        to :data:`_DEFAULT_TOKEN_TTL` so the pull cron re-consents conservatively.
        """
        url = f"{self._base_url}/consent/otp/verify"
        headers = {"x-api-key": self._api_key, "accept": "application/json"}
        payload = {"gstin": gstin, "txn": txn_id, "otp": otp}
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
                resp = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            _log.error("gsp_otp_verify_failed", gstin=gstin, error=str(exc))
            return None
        if resp.status_code != httpx.codes.OK:
            _log.warning("gsp_otp_verify_non_200", gstin=gstin, status=resp.status_code)
            return None
        body = resp.json() or {}
        token = str(body.get("auth_token") or "").strip()
        if not token:
            _log.warning("gsp_otp_verify_no_token", gstin=gstin)
            return None
        expires_at = _parse_expiry(body.get("expiry"))
        return GspAuthToken(token=token, expires_at=expires_at)

    async def file_return(
        self,
        *,
        gstin: str,
        return_type: str,
        return_period: str,
        auth_token: str,
        payload: dict[str, object],
    ) -> FilingReceipt | None:
        """Submit an approved return to GSTN via the GSP.

        INTEGRATION POINT — the save/submit/file sequence (GSTN often splits
        save-then-file), the endpoint, and the ack field names are GSP-specific.
        The generic shape below POSTs the return payload and reads an ``arn``
        back. Wire to the chosen GSP's return-filing API on subscription.
        """
        url = f"{self._base_url}/returns/{return_type.lower()}"
        headers = {
            "x-api-key": self._api_key,
            "x-auth-token": auth_token,
            "accept": "application/json",
        }
        body = {"gstin": gstin, "rtnprd": return_period, "return": payload}
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
                resp = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            _log.error(
                "gsp_file_return_failed",
                gstin=gstin,
                return_type=return_type,
                error=str(exc),
            )
            return None
        if resp.status_code != httpx.codes.OK:
            _log.warning(
                "gsp_file_return_non_200",
                gstin=gstin,
                return_type=return_type,
                status=resp.status_code,
            )
            return None
        data = resp.json() or {}
        arn = str(data.get("arn") or data.get("reference_id") or "").strip()
        if not arn:
            _log.warning("gsp_file_return_no_arn", gstin=gstin)
            return None
        return FilingReceipt(reference_id=arn, status=str(data.get("status") or "filed"))


def _parse_expiry(raw: object) -> datetime:
    """Parse a GSP expiry into a tz-aware datetime, or default the TTL."""
    if isinstance(raw, str) and raw.strip():
        try:
            dt = datetime.fromisoformat(raw.strip())
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC) + _DEFAULT_TOKEN_TTL


def get_gsp_client(settings: Settings) -> GspProvider:
    """Return the real GSP client when configured, else the no-op client."""
    if not settings.is_gsp_configured():
        return NullGspClient()
    assert settings.gsp_base_url is not None  # narrowed by is_gsp_configured
    assert settings.gsp_api_key is not None
    _log.info("gsp_client_active", base_url=settings.gsp_base_url)
    return HttpGspClient(
        base_url=settings.gsp_base_url,
        api_key=settings.gsp_api_key.get_secret_value(),
    )
