# ADR-020: GST Return Filing — Generate → Approve → Submit (Phase 9)

**Status:** Proposed
**Date:** 2026-07-02
**Deciders:** Sarthak

## Context

Enma reconciles and reports today; it does not yet *file*. Phase 9 closes the
last mile — turning the Brain's data into a GSTR-1/3B return and submitting it
to GSTN via the GSP. Filing is the highest-stakes action in the product: a
wrong or unauthorised return is a real-world liability. So the architecture
question is less "can we generate the JSON" and more "how do we make filing
safe."

Forces:
- **Correctness is non-negotiable.** A mis-summed 3B is worse than no 3B. Data
  quality (ledger naming, ITC completeness) varies per firm.
- **A human must own the submission.** The CA is the professional of record;
  the return files under *their* authority, not ours.
- **We already have an approval primitive.** `ENMA APPROVE FILING` locks an
  immutable `filing_approvals` snapshot + hash (the existing flow). Filing
  should build on it, not around it.
- **Submission is externally gated.** GSTN filing goes through the GSP, whose
  API is unchosen — the same dormant-adapter reality as 7b/7c.
- **3B before 1 (for value).** GSTR-3B (the summary payment return) is the
  most tractable, highest-value first target; GSTR-1 (invoice-level detail) is
  larger and can follow on the same rails.

## Decision

A **three-stage pipeline with a mandatory human gate**:

1. **Generate (built).** `services/filing/gstr3b.build_gstr3b_draft` assembles
   a **draft** from the Brain — outward tax from Tally `voucher_sales`, ITC
   from the period's reconciliation — as a `Decimal`-safe structure that
   carries explicit `notes` about every heuristic. Exposed to the CA via the
   `prepare_gstr3b` supervisor tool. **It never files.**
2. **Approve (existing).** The CA reviews the draft and locks it via
   `ENMA APPROVE FILING`; the draft payload becomes the `filing_approvals`
   snapshot. No approval → no submission, structurally.
3. **Submit (dormant).** `GspProvider.file_return` submits an *approved*
   snapshot to GSTN. `Null` by default (Phase 9 ships without auto-submit);
   activates with a GSP + a consented per-client auth token (7b).

## Options Considered

### Option A: Auto-generate and auto-file
| Dimension | Assessment |
|-----------|------------|
| Human safety | Poor — files under our authority, no review |
| Correctness risk | High — heuristic figures filed unchecked |
| Trust/liability | Unacceptable for a compliance product |

**Pros:** maximally "autonomous". **Cons:** disqualifying — removes the
professional gate; one bad summation becomes a filed liability.

### Option B: Generate a draft; CA approves; then submit — **RECOMMENDED**
| Dimension | Assessment |
|-----------|------------|
| Human safety | Strong — explicit approve step, immutable snapshot |
| Correctness risk | Contained — CA verifies flagged figures pre-lock |
| Reuse | High — builds on `ENMA APPROVE FILING` + the GSP adapter |

**Pros:** safe, auditable, reuses primitives, submission cleanly gated.
**Cons:** not one-tap; generation figures are drafts needing CA eyes.

### Option C: Generate only; never submit (hand the CA a draft to file manually)
**Pros:** simplest, zero submission risk. **Cons:** leaves the last mile
manual — the CA still keys/uploads the return on the portal.

## Trade-off Analysis

A optimises "autonomy" at the cost of the one property filing cannot lose —
human authorisation — so it's out. C is safe but stops short of the phase's
goal (close the last mile). B threads the needle: full generation + a
structural human gate (no approval, no submission) + dormant GSP submit that
lights up when the CA and the credentials are both ready. Crucially it reuses
the immutable approval snapshot as the exact artefact submitted, so what the
CA approved is what gets filed — hash-verifiable. The residual risk is
generation *accuracy* (ledger-name heuristics, the ITC proxy), which B
mitigates by surfacing it as reviewable `notes` rather than hiding it.

## Consequences

**Easier:** the CA gets a assembled draft instead of a blank return; the
approved snapshot is submission-ready; activation is keyless; GSTR-1 slots
onto the same generate→approve→submit rails.

**Harder / to revisit:** the GSTR-3B figures are heuristic (tax-ledger naming;
ITC = recoverable-from-recon, not full Table-4 eligible) and must be validated
against real firm data before anyone relies on them; GSTR-1 invoice-level
generation is still to build; the GSP `file_return` save/submit sequence is an
INTEGRATION POINT; per-client filing needs the 7b consent token.

## Action Items
1. [x] `services/filing/gstr3b.build_gstr3b_draft` (draft assembler) +
       `prepare_gstr3b` supervisor tool.
2. [x] Dormant `GspProvider.file_return` (submission INTEGRATION POINT).
3. [ ] Validate the 3B heuristics against real firm Tally exports; refine the
       ITC figure to full Table-4 eligible ITC.
4. [ ] GSTR-1 generation (B2B invoice detail + B2C summary) on the same rails.
5. [ ] Wire post-approval submission: on `ENMA APPROVE FILING`, when the GSP +
       client consent are configured, call `file_return` and record the ARN.
6. [ ] Encrypt the per-client GSP token (shared with the Secrets-Manager move).
