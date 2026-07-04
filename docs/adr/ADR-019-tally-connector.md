# ADR-019: Tally HTTP Connector — Zero-Upload Books Sync (Phase 7c)

**Status:** Proposed
**Date:** 2026-07-02
**Deciders:** Sarthak

## Context

The books leg of the recon arrives as a Tally **Day Book XML** the CA exports
and uploads (ADR-014 parses it into `brain_events(source='tally')`). Tally
ERP runs on the firm's own machine and exposes a **Gateway Server** that
accepts XML requests over HTTP (default `:9000`). Pulling the Day Book
directly removes the export/upload step — the same zero-touch goal as AA
(ADR-018), but for books instead of bank.

Forces:
- **Tally is on-prem.** The Gateway Server listens on the firm's LAN; reaching
  it from our cloud needs either a firm-run tunnel/bridge or an agent.
- **Downstream must not change.** The XML the Gateway returns is the *same*
  voucher XML an upload delivers, so `tally_import` + the ingest path are
  reused verbatim — the connector only replaces *how the bytes arrive*.
- **Version drift.** The TDL/XML request envelope varies slightly across Tally
  releases (Prime vs ERP 9); the report request must tolerate that.
- **Commercial/ops gating.** Requires the firm to expose their Gateway; ship
  dormant, activate on a connector URL (the provider pattern).

## Decision

Model the connector as a **dormant, env-gated provider**
(`providers/tally_connector.py`) exposing `fetch_day_book(company, from, to)`
that POSTs the standard Gateway **Export → Day Book** XML request and returns
the voucher XML bytes — fed to the **existing `tally_import` parser**
unchanged. `Null`-by-default; activate with `TALLY_CONNECTOR_URL` (+ optional
`TALLY_CONNECTOR_API_KEY` for a hosted bridge that fronts the on-prem Tally
with auth). The request-envelope builder is isolated so per-version tweaks are
one function.

## Options Considered

### Option A: Keep manual Day Book export + upload
| Dimension | Assessment |
|-----------|------------|
| Complexity | Low (built) |
| Cost | Free |
| Effort (human) | High — export + upload every period |
| Freshness | Stale — only as current as the last upload |

**Pros:** shipped, no infra. **Cons:** the manual step this phase removes.

### Option B: Direct cloud→Gateway over the public internet
**Pros:** no bridge component. **Cons:** exposing Tally's port publicly is a
security non-starter; dynamic firm IPs; brittle.

### Option C: HTTP connector via a firm-run bridge/tunnel, dormant provider — **RECOMMENDED**
| Dimension | Assessment |
|-----------|------------|
| Complexity | Med — request envelope + a small bridge the firm runs |
| Cost | Low (bridge is lightweight); no per-call fees |
| Effort (human) | One-time bridge setup, then zero |
| Freshness | On-demand / scheduled pulls |

**Pros:** reuses the parser + ingest path; no public Tally exposure; keyless
activation. **Cons:** the firm runs a small bridge/agent; XML envelope is
version-sensitive (the marked INTEGRATION POINT).

### Option D: E-invoice IRN pull instead of Tally
**Pros:** cloud-native, no on-prem. **Cons:** covers only e-invoiced B2B
supply, not the full Day Book (expenses, non-e-invoice vouchers) — a
complement, not a replacement.

## Trade-off Analysis

A leaves the manual step in place. B trades a bridge component for an
unacceptable security posture (public on-prem port). C keeps Tally private
behind a firm-run bridge while reusing the entire existing books pipeline —
the connector is a *transport swap*, not a new ingestion path, so risk is
contained to one adapter. D is real but partial and belongs alongside, not
instead. C wins; its only genuine cost is the version-specific request
envelope, isolated in `_day_book_request_xml`.

## Consequences

**Easier:** books stay current without exports; scheduled pulls fit the cron
fan-out; parser/ingest untouched; keyless activation.

**Harder / to revisit:** the firm must run/host a bridge to their Gateway;
per-Tally-version envelope validation; company-name ↔ client mapping (reuse
`_resolve_tally_client`); scheduling cadence vs Tally load.

## Action Items
1. [x] Dormant `providers/tally_connector.py` (Protocol + Null + Http) +
       `TALLY_CONNECTOR_URL`/`_API_KEY` config + `is_tally_connector_configured()`.
2. [ ] Ship/spec the firm-run bridge (or document a reverse-tunnel setup) and
       validate the Day Book XML envelope against Tally Prime + ERP 9.
3. [ ] A Tally-pull cron (mirror the ingest fan-out) → `tally_import` →
       `brain_events(source='tally')`, routed per client by company name.
4. [ ] (Complement) e-invoice IRN pull for cloud-native B2B supply.
