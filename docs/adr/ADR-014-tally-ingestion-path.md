# ADR-014 — Tally Ingestion Path (Layer A · P1 · unit A2)

**Status:** Proposed
**Date:** 2026-06-23
**Deciders:** Sarthak (founder), Engineering
**Supersedes / relates:** builds on the `brain_events` substrate (migration 013, ADR-012 P0 brain layer). Converges later with **P13 Ghost Layer** (Tauri desktop overlay).

---

## Context

P1 (Brain Ingestion v1) feeds real firm data into the `brain_events` table shipped in A1. The first and highest-leverage source is **Tally**, because every Indian CA already lives in it — it is the system of record for vouchers, ledgers, and GST data.

The structural problem is **locality**:

- **Enma backend is cloud** — AWS ECS Fargate, `enma-prod`, ap-south-1.
- **Tally Prime is on-prem** — it runs on the CA's Windows desktop or a LAN machine. Its programmatic surfaces (HTTP/XML gateway and ODBC, both on port 9000) listen on `localhost` / the LAN only. A cloud service cannot reach them without the CA punching an inbound hole or running a bridge.

Constraints that shape the answer:

- **Beta is 5–6 CAs, starting now, founder doing daily check-ins.** Friction on day one (installing/running software) kills the feedback loop that beta exists for.
- **The brain must compound from day one** — the whole reason P0/P1 precede revenue work. Ingestion that produces nothing real for weeks defeats the sequencing.
- **Rules in force:** deterministic math (Python parses Tally, no LLM); `BaseQuery` tenant scoping; Decimal-only money; idempotent re-ingest; DPDP (no *new* external data sharing).
- The doc already marks **Tally ODBC as W5+** and **P13 desktop as optional/deferred**.

## Decision

**Adopt Option A — manual Tally XML export upload, parsed deterministically into `brain_events` — for P1/beta. Document Option B (local connector agent) as the continuous-sync upgrade, deferred until a CA needs it; it reuses A's parser and converges with P13.**

The CA runs Tally's standard *Day Book → Export → XML* (a workflow CAs already know), and sends the file through the **existing document upload path** (Telegram/WhatsApp/web). The backend sniffs that the file is Tally XML, branches to a Tally ingestion handler, parses it in pure Python, and writes one `brain_event` per voucher via `record_dedup` (`dedup_key` = voucher GUID, `occurred_at` = voucher date).

## Options Considered

### Option A — Manual Tally XML export upload (parse → brain_events)

CA exports Day Book/vouchers as Tally XML, uploads it; backend parses and ingests.

| Dimension | Assessment |
|-----------|------------|
| Complexity | **Low** — one parser + a branch in the existing pipeline |
| Cost | $0 — no new infra, no software to distribute |
| CA effort | Manual export+upload (a known CA workflow); not continuous |
| Scalability | Fine for beta volumes; bulk insert handles a month of vouchers |
| Team familiarity | High — symmetric to `tally.py` which already *writes* Tally XML |

**Pros:** zero CA install; real `brain_events` now; deterministic (honors "Python does math"); parser is **reused by Option B later** (not throwaway); re-uploads are idempotent via voucher GUID.
**Cons:** not continuous — CA initiates each import; relies on the CA exporting correctly.

### Option B — Local connector agent (push model)

A small headless agent on the CA's machine polls the local Tally HTTP gateway (`localhost:9000`) and POSTs HMAC-signed deltas to an Enma cloud endpoint.

| Dimension | Assessment |
|-----------|------------|
| Complexity | **High** — build, code-sign, distribute, auto-update, support a desktop binary |
| Cost | Engineering + ongoing support; Windows signing cert |
| CA effort | One-time install, then zero — continuous sync |
| Scalability | Best long-term — no manual step, near-real-time brain |
| Team familiarity | New surface (desktop dist) — this *is* P13 territory |

**Pros:** continuous, hands-off after install; the end-state for serious shops.
**Cons:** install friction at the worst time (beta kickoff); desktop distribution/support burden; overlaps P13 which the doc marks optional. **Reuses Option A's XML parser** — so doing A first costs nothing toward B.

### Option C — Direct cloud→Tally over a tunnel (ngrok/VPN to :9000)

Enma cloud calls the CA's Tally gateway through a tunnel.
**Rejected:** exposes accounting software to inbound calls, fragile per-CA tunnel, security/DPDP nightmare.

### Option D — Third-party Tally-cloud aggregator (Suvit / Biz Analyst / TallyConnector SaaS)

Buy ingestion from a vendor API.
**Rejected for v1:** vendor lock-in + per-seat cost, hands our brain's raw substrate to a third party (DPDP + moat concerns), loss of control over the exact data shape. Revisit only if manual export becomes the bottleneck at scale.

## Trade-off Analysis

The real axis is **continuous-but-heavy (B) vs. manual-but-instant (A)**. For 5–6 CAs with a founder on daily calls, the value is *learning whether the brain produces useful recall*, not *automating the last mile of sync*. Option A gets real Tally data into `brain_events` this week with zero install, and — critically — its XML parser is the exact component Option B's agent needs, so choosing A does not waste a line of work toward B. Option A is the cheapest path that is also strictly on the way to the eventual answer.

The cost of A (manual export) is acceptable precisely because beta CAs are high-touch; the moment one of them finds manual export tedious, that's the signal to build B (and pull P13 forward).

## Consequences

- **Easier:** real Tally vouchers in the brain immediately; deterministic, auditable ingestion; idempotent re-imports; one upload UX for the CA; parser reused by P13.
- **Harder:** ingestion is CA-initiated, not continuous; we depend on the CA's export being complete for a period.
- **Revisit when:** a CA wants hands-off sync, or voucher volume makes manual export impractical → build Option B (converges with P13 Ghost Layer). Reconsider Option D only if multi-hundred-firm scale outpaces self-export.

## Implementation Notes (binds A2)

- **Parser:** `app/services/tally_import.py` — sibling to the existing `tally.py` writer. Use **`defusedxml`** (XXE protection — the file is user-supplied) and tolerate Tally's quirks: BOM/UTF-16/Windows-1252 encodings and stray unescaped `&`. Output normalised voucher dicts.
- **Mapping:** one `brain_event` per `<VOUCHER>`. `source='tally'`, `event_type=f'voucher_{type}'` (sales/purchase/payment/receipt/journal/…), `dedup_key`=`<GUID>`, `occurred_at`=`<DATE>` (the *voucher* date — it is part of the dedup key, so re-exports collapse), `payload`={voucher_type, party, narration, amount (Decimal **as string**), ledger_entries[], gst}. Money via `parse_money`; never float.
- **Bulk insert:** a monthly Day Book is hundreds–thousands of vouchers. Add `BrainEventQuery.record_dedup_bulk` — a single `INSERT … ON CONFLICT DO NOTHING RETURNING` so we don't do N round-trips, and can report `(inserted, skipped)` counts.
- **Routing to a client:** a Tally company = one Enma client. Match `<SVCURRENTCOMPANY>`/company name against the active client list (reuse the identity-resolver fuzzy match); on ambiguity reuse the existing pending-assignment "which client?" prompt. CA can override via upload caption.
- **Pipeline wiring:** content-sniff in `_run_document_pipeline` — if the file is Tally XML (`<ENVELOPE>`+`<TALLYMESSAGE>`), branch to `_run_tally_ingestion` instead of the invoice extractor. Keeps "just send Enma the file" as the only UX.
- **Not billable:** ingestion writes no `outcome_unit` (outcomes are billable artefacts; an import is not). No trajectory/confidence rows — this is deterministic ingestion, not an agent turn.
- **DPDP:** Tally data stays inside the firm's tenant boundary; no new external sharing, so the firm's existing DPA covers it (contrast Liaison outreach, which needs `liaison_consent_at`).

## Action Items

1. [ ] `app/services/tally_import.py` — defusedxml, encoding-tolerant parser → normalised voucher dicts (+ unit tests with a real Day Book XML fixture).
2. [ ] `BrainEventQuery.record_dedup_bulk` — batched `ON CONFLICT DO NOTHING RETURNING`, returns `(inserted, skipped)`.
3. [ ] Client routing — company-name fuzzy match + caption override + pending-assignment reuse on ambiguity.
4. [ ] Content-sniff branch in `_run_document_pipeline` → `_run_tally_ingestion`.
5. [ ] CA confirmation reply: "Imported N vouchers for {client} (M new, K already on file)."
6. [ ] Deploy (digest-pinned) + smoke: export a real Day Book from a beta CA's Tally, upload, confirm `brain_events` rows land and a re-upload is a no-op.
7. [ ] Defer Option B agent; note the convergence with P13 in the backlog.
