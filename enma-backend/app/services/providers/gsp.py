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

from typing import Final, Protocol, runtime_checkable

import httpx

from app.config import Settings
from app.logging_setup import get_logger

__all__ = [
    "GspProvider",
    "HttpGspClient",
    "NullGspClient",
    "get_gsp_client",
]

_log = get_logger(__name__)

_REQUEST_TIMEOUT_S: Final[float] = 30.0


@runtime_checkable
class GspProvider(Protocol):
    """Pulls a client's GSTR-2B JSON for a return period from a GSP."""

    @property
    def is_configured(self) -> bool: ...

    async def fetch_gstr2b(
        self, *, gstin: str, return_period: str, auth_token: str
    ) -> bytes | None:
        """Return the GSTR-2B JSON bytes, or ``None`` when unavailable."""
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
