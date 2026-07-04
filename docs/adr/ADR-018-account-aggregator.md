# ADR-018: Account Aggregator — Zero-Touch Bank Data (Phase 7c)

**Status:** Proposed
**Date:** 2026-07-02
**Deciders:** Sarthak

## Context

The bank leg of the Tri-Way recon (ADR-016) currently arrives by manual
upload or the Phase-7a IMAP forward. Both still need a human to send the
statement. The RBI/Sahamati **Account Aggregator (AA)** framework lets a
registered **FIU** (Financial Information User) pull a customer's bank data
directly after a one-time, revocable, consumer-granted consent — the true
zero-touch path ADR-017 flagged as the endgame.

Forces:
- **Consent is law, not preference.** AA data flows only against a live
  consent artefact the taxpayer approves in their AA app; no scraping, no
  stored bank credentials.
- **We are not an AA.** Enma is an FIU; we integrate *through* an AA gateway
  (Finvu / OneMoney / CAMSFinserv, or a TSP that fronts several).
- **Downstream must not change.** Ingestion already normalises statement
  bytes → `brain_events(source='bank')` → 180-day recon. AA should feed that
  same seam, not fork it.
- **Commercial gating.** FIU registration + a gateway subscription are
  business steps; the code must ship dormant and activate on credentials
  (the established provider pattern, `providers/base.py`).

## Decision

Model AA as a **dormant, env-gated provider** (`providers/account_aggregator.py`)
with the two-step AA contract — `request_consent` (one-time, per client) →
`fetch_transactions` (recurring, per period) — whose output is **normalised
statement bytes fed to the existing `auto_ingest` bank seam**. Ship it
`Null`-by-default; activate with `AA_BASE_URL` + `AA_API_KEY`. Persist a
per-client `consent_id` (mirrors the GSP `gsp_auth_token` pattern) so the
recurring pull needs no re-consent until the artefact expires/revokes.

## Options Considered

### Option A: Continue with upload + IMAP forward only
| Dimension | Assessment |
|-----------|------------|
| Complexity | Low (already built) |
| Cost | Free |
| Effort (human) | High — a person forwards every statement |
| Coverage | Partial — password-PDF friction, missed months |

**Pros:** shipped; no partner needed. **Cons:** not zero-touch; the exact gap
this phase closes.

### Option B: Screen-scrape / net-banking credentials
**Pros:** no partner. **Cons:** disqualifying — ToS-violating, insecure
(stored credentials), brittle, and unacceptable for a compliance product.

### Option C: AA via a gateway/TSP, as a dormant FIU provider — **RECOMMENDED**
| Dimension | Assessment |
|-----------|------------|
| Complexity | Med — consent lifecycle + FI-schema normalisation |
| Cost | FIU onboarding + per-consent/per-fetch gateway fees |
| Effort (human) | Near-zero after the one-time consent tap |
| Coverage | Broad — all AA-enrolled banks, scheduled pulls |

**Pros:** legally clean, recurring, hands-off, reuses the ingest seam.
**Cons:** partner + registration lead time; FI JSON→statement normalisation
is gateway-specific (the marked INTEGRATION POINT).

## Trade-off Analysis

A and B are non-starters for *zero-touch + compliant*: A keeps a human in the
loop; B is legally and operationally radioactive. C is the only path that is
both consent-clean and recurring. Its real cost is integration specificity —
the consent request payload and the FI-schema→statement normalisation differ
per gateway — which the provider pattern quarantines behind one adapter and
one `is_configured` gate, so the rest of the system is untouched until a
gateway is chosen and the two INTEGRATION POINTs are filled in.

## Consequences

**Easier:** truly hands-off bank data; scheduled pulls slot into the existing
cron fan-out; downstream recon unchanged; activation is keyless.

**Harder / to revisit:** FIU registration + gateway contract; a per-client
consent lifecycle (grant, expiry, revocation, re-consent); FI-schema mapping;
the `consent_id` should be encrypted at rest (shared follow-up with the GSP
token and Secrets-Manager migration).

## Action Items
1. [x] Dormant `providers/account_aggregator.py` (Protocol + Null + Http),
       `AA_BASE_URL`/`AA_API_KEY` config + `is_account_aggregator_configured()`.
2. [ ] Choose the AA gateway / TSP; fill the two INTEGRATION POINTs (consent
       request payload; FI-fetch + FI→statement normalisation).
3. [ ] Per-client `aa_consent_id` column + a `/aa-consent` CA flow (mirror the
       7b `/consent` OTP handshake) to capture and store it.
4. [ ] A recurring AA-pull cron (mirror `cron_gstr2b_pull`) feeding
       `auto_ingest.ingest_bank_statement_bytes`.
5. [ ] Encrypt the stored consent handle (with the GSP-token migration).
