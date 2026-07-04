"""Tally HTTP connector adapter (Track-A automation, Phase 7c) — ADR-019.

Zero-upload books sync. Tally's Gateway Server accepts XML requests over HTTP
(default port 9000); this adapter pulls a company's Day Book (vouchers) for a
date range and hands back the same voucher XML an upload delivers, so it feeds
the existing ``tally_import`` parser → ``brain_events(source='tally')`` path
unchanged — the CA never exports/uploads a Day Book by hand again.

Dormant by default: with no ``TALLY_CONNECTOR_URL`` in the env,
:func:`get_tally_connector_client` returns :class:`NullTallyConnectorClient`
and nothing fires. Point it at a firm's Tally Gateway Server (or a hosted
bridge to it) to activate.

INTEGRATION POINT — the exact TDL/XML request envelope (the ``EXPORT``/
``Day Book`` collection request) is Tally-version-specific; the shape below is
the common Gateway request. The optional API key supports a hosted bridge that
fronts the on-prem Tally with auth.
"""

from __future__ import annotations

from datetime import date
from typing import Final, Protocol, runtime_checkable

import httpx

from app.config import Settings
from app.logging_setup import get_logger

__all__ = [
    "HttpTallyConnectorClient",
    "NullTallyConnectorClient",
    "TallyConnectorProvider",
    "get_tally_connector_client",
]

_log = get_logger(__name__)

_REQUEST_TIMEOUT_S: Final[float] = 60.0


def _day_book_request_xml(*, company: str, from_date: date, to_date: date) -> str:
    """Build the Tally Gateway XML EXPORT request for a company's Day Book.

    INTEGRATION POINT — report name / SVFROMDATE-SVTODATE formatting can vary
    by Tally release; this is the standard Day Book export envelope.
    """
    return (
        "<ENVELOPE><HEADER><TALLYREQUEST>Export Data</TALLYREQUEST></HEADER>"
        "<BODY><EXPORTDATA><REQUESTDESC>"
        "<REPORTNAME>Day Book</REPORTNAME>"
        "<STATICVARIABLES>"
        f"<SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY>"
        f"<SVFROMDATE>{from_date.strftime('%Y%m%d')}</SVFROMDATE>"
        f"<SVTODATE>{to_date.strftime('%Y%m%d')}</SVTODATE>"
        "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
        "</STATICVARIABLES></REQUESTDESC></EXPORTDATA></BODY></ENVELOPE>"
    )


@runtime_checkable
class TallyConnectorProvider(Protocol):
    """Pulls a company's Day Book voucher XML from a Tally Gateway Server."""

    @property
    def is_configured(self) -> bool: ...

    async def fetch_day_book(
        self, *, company: str, from_date: date, to_date: date
    ) -> bytes | None:
        """Return the Day Book voucher XML bytes, or ``None`` when unavailable."""
        ...


class NullTallyConnectorClient:
    """No-op adapter used when no Tally connector is configured (the default)."""

    @property
    def is_configured(self) -> bool:
        return False

    async def fetch_day_book(
        self, *, company: str, from_date: date, to_date: date
    ) -> bytes | None:
        return None


class HttpTallyConnectorClient:
    """Real Tally connector — active only when the connector URL is present."""

    def __init__(self, *, base_url: str, api_key: str | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return True

    async def fetch_day_book(
        self, *, company: str, from_date: date, to_date: date
    ) -> bytes | None:
        body = _day_book_request_xml(
            company=company, from_date=from_date, to_date=to_date
        )
        headers = {"content-type": "text/xml"}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
                resp = await client.post(self._base_url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            _log.error("tally_connector_fetch_failed", company=company, error=str(exc))
            return None
        if resp.status_code != httpx.codes.OK:
            _log.warning(
                "tally_connector_non_200", company=company, status=resp.status_code
            )
            return None
        return resp.content


def get_tally_connector_client(settings: Settings) -> TallyConnectorProvider:
    """Return the real Tally connector when configured, else the no-op client."""
    if not settings.is_tally_connector_configured():
        return NullTallyConnectorClient()
    assert settings.tally_connector_url is not None
    key = (
        settings.tally_connector_api_key.get_secret_value()
        if settings.tally_connector_api_key
        else None
    )
    _log.info("tally_connector_client_active", base_url=settings.tally_connector_url)
    return HttpTallyConnectorClient(base_url=settings.tally_connector_url, api_key=key)
