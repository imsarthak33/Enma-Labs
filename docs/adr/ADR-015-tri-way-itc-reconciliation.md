# ADR-015 — Tri-Way ITC Reconciliation (Track A · TA-2)

**Status:** Accepted
**Date:** 2026-06-24
**Deciders:** Sarthak (founder), Engineering
**Relates:** first *operational consumer* of the Brain (`brain_events`, A1/A2). Builds on the manual-upload ingestion pattern from [ADR-014](ADR-014-tally-ingestion-path.md). Pricing hook into `outcome_units` (P0 / TA-1).

---

## Context

This is the feature that makes the Brain pay for itself. Until now ingestion (`brain_events`) has had no operational reader — A5 only echoes it back. Tri-Way ITC reconciliation is the first thing that genuinely needs integrated, durable, multi-source state, and it is the first thing a CA will pay for: **recovered ITC ₹ is the Track A pricing unit.**

Reconciliation cross-checks a client's purchases for a filing period across three sources:

1. **Invoices** — what the client is *claiming* as input tax credit.
2. **GSTR-2B** — what GSTN says is *available* to claim (auto-drafted from suppliers' filings).
3. **Bank** — what was actually *paid* (Section 16(2): ITC must be reversed if the supplier isn't paid within 180 days).

The mismatches are the money:
- **In books, not in 2B** → supplier hasn't filed → ITC at risk; reverse or chase the vendor.
- **In 2B, not in books** → a purchase the client forgot to claim → **recoverable ITC** (the billable outcome).
- **Amount mismatch** → claim the lower of the two; flag the delta.
- **Claimed but unpaid > 180 days** → mandatory reversal (the bank leg).

None of this is a RAG/LLM problem — it is deterministic matching over structured ledgers. **Python does the math; the LLM never reconciles** (a hallucinated match is a wrong tax filing).

**Access reality (the ADR-014 lesson applies):** both new sources are *manually downloadable* by the CA — GSTR-2B as JSON/Excel from the GST portal, bank as a statement export. So neither needs a GSP licence or scraping for the MVP: the CA uploads them, exactly like the Tally export. Continuous GSTN/bank-feed sync is a later upgrade.

## Decision

**Build a deterministic reconciliation engine, phased: invoice ↔ GSTR-2B first (the monthly 2A/2B recon every CA already does), then add the bank/180-day leg.** GSTR-2B and bank statements are ingested via manual upload into `brain_events`, reusing the Tally sniff-on-upload path. The match key is the existing `documents.content_hash` formula. Recovered ITC is recorded as `outcome_units(kind='itc_recovered_inr')`, wiring recon directly to Track A pricing.

The "invoice" leg is the **`documents`** table (Enma's OCR'd purchases — already structured, carries `tax_verdict` and `content_hash`). The Tally `brain_events` become an optional *books-vs-invoices* cross-check in a later phase, not the MVP leg.

## Options Considered

### 1. Scope — full tri-way at once vs. phased (2-way first)

| | Full tri-way now | **Phased: 2-way → +bank (recommended)** |
|---|---|---|
| Value delivered | Complete, but slower to ship | ~80% of the value (invoice↔2B is the recon CAs do monthly) ships first |
| Complexity | Bank parsing + 180-day logic + a new `brain_events` source upfront | Bank leg deferred; MVP needs no schema change |
| Risk | Larger surface before any CA validation | Validate the match engine on real 2B data first |

**Phased wins:** the invoice↔2B leg is where the recoverable-ITC money is and what a beta CA recognises instantly. The bank leg (payment evidence, 180-day reversal) is real but secondary and can land once matching is proven.

### 2. Invoice-leg source — `documents` vs Tally `brain_events` vs both

- **`documents` (recommended):** structured, has `tax_verdict` + the `content_hash` match key already computed. It *is* what Enma processed.
- **Tally `brain_events`:** the CA's books — ideal eventually, but voucher payloads aren't keyed for matching yet and not every firm uploads Tally.
- **Both:** a books-vs-invoices cross-check is genuinely useful but is a *fourth* comparison — defer.

**Use `documents` for MVP.** Tally cross-check is a fast follow once the engine exists.

### 3. GSTR-2B ingest — manual upload vs GSP API

- **Manual JSON/Excel upload (recommended):** CA downloads 2B from the portal, uploads it; we sniff + parse into `brain_events(source='gstn_portal', event_type='gstr2b_entry')`. Zero licence, reuses the Tally upload path.
- **GSP API:** continuous, but needs a licensed GST Suvidha Provider integration — cost + onboarding lead time. Defer to a later "continuous sync" ADR.

### 4. Matching — deterministic vs LLM-assisted

**Deterministic only.** Match on `SHA-256(UPPER(supplier_gstin) | UPPER(invoice_no) | invoice_date)` — the exact `documents.content_hash` formula — with a fuzzy fallback on normalised invoice number + amount tolerance (₹1) for format drift. The LLM is never in the matching loop.

## Trade-off Analysis

The single most important call is **phasing**. "Tri-way" is the right end-state, but the invoice↔2B leg alone is the product a CA pays for on day one, and it's the cleanest validation of the match engine against messy real-world 2B data. Shipping it first de-risks the hard part (matching) before layering on the bank leg's payment-tracking complexity. Reusing `content_hash` as the match key means the matching core is small and already battle-tested by the dedup system.

## Consequences

- **Easier:** the Brain finally has an operational reader; recon output feeds `outcome_units` so pricing becomes real; GSTR-2B lands in `brain_events` for reuse by future GSTR-1/3B filing (TA-3).
- **Harder:** GSTR-2B JSON is fiddly and versioned; the parser must be defensive. The bank leg will need a new `brain_events` source (`'bank'`) → a CHECK-constraint migration when that phase lands.
- **Revisit:** continuous GSTN/bank sync (GSP licence) when manual upload becomes the bottleneck; Tally-books-vs-invoices as a fourth leg.

## MVP Scope (this build)

1. **GSTR-2B ingest:** sniff an uploaded GSTR-2B JSON in the document pipeline → `gstr2b_import.py` parser (defusedxml not needed — JSON; use stdlib `json`, defensive) → `brain_events(source='gstn_portal', event_type='gstr2b_entry')` via `record_dedup_bulk` (dedup_key = supplier GSTIN|invoice|date, occurred_at = invoice date).
2. **Recon engine** `app/services/recon.py` (pure, deterministic): given a client + period, load `documents` (invoice leg) + `gstr2b_entry` brain_events (available leg), match on `content_hash` with fuzzy fallback, classify each line into matched / in_books_not_in_2b / in_2b_not_in_books / amount_mismatch.
3. **Supervisor tool** `reconcile_itc(client, month, year)` → runs the engine → delivers a CSV report + summary message → records `outcome_units(kind='reconciled_period')` and, for recoverable lines, `kind='itc_recovered_inr'`.
4. **Audit:** `reconciliation_runs` table (immutable summary per run, mirrors `tally_export_runs`).
5. **No bank leg, no new `brain_events` source** in the MVP — keeps it migration-light (only the `reconciliation_runs` table).

## Action Items

1. [ ] Migration 016 — `reconciliation_runs` (firm/client-scoped, immutable audit: period, counts per bucket, recoverable_itc total, file hash).
2. [ ] `app/services/gstr2b_import.py` — defensive GSTR-2B JSON parser → normalised entries (+ tests with a real 2B fixture).
3. [ ] Wire GSTR-2B sniff into `_run_document_pipeline` (alongside the Tally branch) → ingest to `brain_events`.
4. [ ] `app/services/recon.py` — deterministic 2-way match engine (content_hash + fuzzy fallback, ₹1 tolerance) → classified result.
5. [ ] `reconcile_itc` supervisor tool → CSV report + summary + `outcome_units`.
6. [ ] Tests: match engine (matched / missing-either-side / amount-mismatch) + outcome recording.
7. [ ] Deploy from the `deploy/a2-tally` lineage; smoke with a real 2B + the CLEIND documents.
8. [ ] **Phase 2 (separate ADR):** bank leg — `'bank'` brain_events source (CHECK migration), 180-day reversal logic, statement parser.

> **Need from you:** a real **GSTR-2B JSON** export (any client, any month) to build the parser against — same as the CLEIND Tally fixture. Without it I'll build to the published GSTN schema and we validate on first upload.
