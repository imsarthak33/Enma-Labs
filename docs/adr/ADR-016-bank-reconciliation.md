# ADR-016 — Bank Reconciliation (Track A · TA-2 phase 2 · the 180-day leg)

**Status:** Accepted
**Date:** 2026-06-24
**Deciders:** Sarthak (founder), Engineering
**Relates:** completes the "Tri-Way" of [ADR-015](ADR-015-tri-way-itc-reconciliation.md). Adds the third leg (bank/payment) to the invoice ↔ GSTR-2B engine already live (prod rev 33). Manual-upload ingestion pattern from [ADR-014](ADR-014-tally-ingestion-path.md).

---

## Context

ADR-015 shipped the invoice ↔ GSTR-2B legs. The third leg is **payment evidence**, and it exists because of one specific law:

> **Section 16(2), second proviso (CGST Act):** if a registered recipient does not pay the supplier (invoice value + tax) within **180 days** of the invoice date, the ITC already claimed must be **reversed** (added back to output liability, with interest).

So the bank leg answers: *for each purchase where the client claimed ITC, is there evidence it was actually paid — and if not, is it past 180 days (mandatory reversal) or approaching it (act now)?* This is at-risk ITC the CA must catch before it becomes an interest-bearing liability.

Same access reality as the rest of TA-2: the CA exports a **bank statement** (CSV/Excel) and uploads it — no banking API, no licence.

## The hard truth about bank data (stated upfront)

Unlike GSTR-2B (one published JSON schema), **bank statements have no standard format** — every bank's CSV differs, and the payment rows carry no GSTIN and no invoice number. Narration is free text ("NEFT-ACME SUPPLIES-UTR123", "CHQ 456789"). So invoice→payment matching is *fundamentally fuzzier* than invoice↔2B and will never be 100% automatic. The design accepts this: the engine proposes matches and flags the residue for CA review; it never silently asserts a payment happened.

## Decision

**Build the bank leg as a deterministic, fuzzy-tolerant matcher focused on the 180-day reversal flag — the high-value, tractable output — not a perfect payment-ledger reconciliation.** Bank statements are ingested via a header-tolerant CSV parser into `brain_events(source='bank')`; the matcher pairs purchase invoices to outbound (debit) payments on **amount (±tolerance) + vendor-name-in-narration (fuzzy) + within-180-days**, then classifies each claimed invoice as **paid / unpaid (within 180d) / unpaid (over 180d → REVERSAL)**. The output is a CSV + a reversal-risk ITC total; matching is advisory, never a silent assertion.

## Options Considered

### 1. Bank statement format — canonical CSV vs LLM column-mapping vs per-bank parsers

| | **Header-tolerant canonical CSV (recommended)** | LLM column-mapping | Per-bank parsers |
|---|---|---|---|
| Effort | Low — one parser, fuzzy header detection | Medium — LLM maps columns then deterministic parse | High — N parsers, endless tail |
| Risk | CA may need to tweak headers | LLM in the data path (mapping only) | Maintenance sink |
| Fit for beta | Best — most exports already have Date/Narration/Debit/Credit | Overkill now | No |

**Recommended:** detect common Indian-bank column names (`Txn Date`/`Value Date`/`Date`, `Narration`/`Particulars`/`Description`, `Withdrawal`/`Debit`/`Withdrawal Amt.`, `Deposit`/`Credit`). Falls back to a clear "couldn't find the columns — here's the format I expect" message. LLM mapping is a later upgrade if real statements prove too varied.

### 2. Match strategy — exact vs fuzzy

Bank rows have no GSTIN/invoice-no, so **exact keying is impossible**. Match on **amount within tolerance + supplier-name token overlap in narration + date window ≤180d**. A single confident match marks the invoice paid; ambiguous/absent leaves it unpaid for review. Deterministic (token/amount/date math) — no LLM in the match loop.

### 3. What's the output / outcome unit

The bank leg surfaces **at-risk ITC** (claimed but unpaid >180d), not recoverable ITC. Record the run on `reconciliation_runs` (extend `kind='bank_180day'`); the reversal total feeds the CA's action list, not a new billable recovery. No new `outcome_units` kind needed (reuse `reconciled_period`).

## Consequences

- **Easier:** the CA gets an automatic 180-day reversal watchlist — a real liability caught before interest accrues; the bank data lands in `brain_events` for future cash-flow features (P12).
- **Harder:** fuzzy matching means false negatives (a real payment the narration didn't reveal) → the report must frame unpaid as "no payment **found**", not "unpaid", and let the CA confirm. Bank-format drift will need parser tweaks.
- **Schema:** `brain_events` needs a new `'bank'` source value → CHECK-constraint migration (017), plus the `_ALLOWED_SOURCES` set in `BrainEventQuery`.

## MVP Scope (this build)

1. **Migration 017** — add `'bank'` to the `brain_events` source CHECK; extend `kind` note on `reconciliation_runs` (no DDL, just usage). Update `_ALLOWED_SOURCES`.
2. **`app/services/bank_import.py`** — header-tolerant bank-statement CSV parser → normalised `BankTxn(date, narration, amount, direction)`; `looks_like_bank_csv` sniff; Decimal money.
3. **`app/services/bank_recon.py`** (pure) — match claimed purchase invoices ↔ debit txns (amount ±tol + narration token overlap + ≤180d) → classify paid / unpaid_within_180 / unpaid_over_180; reversal-risk ITC total; CSV builder.
4. **Wire** the bank-CSV sniff into the document pipeline → ingest to `brain_events(source='bank')` + run bank recon → CSV + summary. Plus a `reconcile_bank(client)` supervisor tool.
5. **Tests** — parser (header variants) + matcher (paid / >180d reversal / fuzzy narration).

Built to a canonical/common-Indian-bank CSV (no real statement fixture yet — validate on first upload, same as GSTR-2B). LLM column-mapping and true per-payment ledger recon are later upgrades.

## Action Items

1. [ ] Migration 017 — `'bank'` brain_events source (drop+re-add CHECK, idempotent).
2. [ ] `bank_import.py` — header-tolerant CSV parser + sniff + tests.
3. [ ] `bank_recon.py` — fuzzy 180-day matcher + CSV + tests.
4. [ ] Worker sniff → ingest(`source='bank'`) + auto bank-recon; `reconcile_bank` supervisor tool.
5. [ ] alembic 017 on port 5432; deploy from `deploy/a2-tally`; smoke on a real statement.
