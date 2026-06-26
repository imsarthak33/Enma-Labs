# ADR-017: Client Document-Ingestion & Chase Channel

**Status:** Accepted
**Date:** 2026-06-26
**Deciders:** Sarthak

## Context

Enma needs each client's source documents (bank statement first; later
sales/purchase registers) to flow in with minimal human effort, and to
**proactively chase** clients who haven't sent them before a filing
deadline. This must work for both tracks — **Track A** (the CA's clients,
who are *not* Enma users) and **Track B** (Enma's own business clients,
who *are*).

Forces:
- **Indian SMB reality:** WhatsApp-dominant, some Telegram, many
  email-averse — but most will *tap a link*.
- **Compliance product:** no WhatsApp-ToS-violating web automation (a banned
  number mid-filing-season is catastrophic); DPDP consent must be clean.
- **Deterministic routing:** an inbound document must map to *exactly* the
  right client — no fuzzy guessing on tax data.
- **Low ops burden:** the CA shouldn't babysit infrastructure per client.
- **Reuse what exists:** onboarding deep-link binding (`/start <uuid>`), the
  messaging factory, `brain_events`, and the per-client email-ingest address.

The originally-floated idea — a **shared group** (client + CA + Enma) —
triggered this review because group management + routing get messy at scale.

## Decision

**Reject the group model.** Adopt a **hybrid of two deterministic channels**,
no groups:

1. **Email-ingest address** (passive) — `client-<uuid>@ingest.enmalabs.in`,
   for bank/CA auto-forward. *Already built (Phase 6).*
2. **1:1 bot DM, deep-link-bound** (interactive + chase) — the client taps a
   per-client link (`t.me/enmabot?start=client-<token>` / WhatsApp
   click-to-chat) which **binds that 1:1 chat to the client**, reusing the
   exact mechanism CAs already onboard with. Enma then DMs them, and they can
   drop files directly in the 1:1 chat.

## Options Considered

### Option A: Shared group (client + CA + Enma)
| Dimension | Assessment |
|-----------|------------|
| Complexity | High — manage N groups, map group→client, membership churn |
| Cost | Free (Telegram) |
| Scalability | Poor — group admin per client |
| Platform support | Telegram only — WhatsApp Business API cannot bot a group |

**Pros:** transparency; one place for chase + drop.
**Cons:** impossible on WhatsApp via official API; group-creation friction;
brittle routing; CA-side mess; weaker consent story.

### Option B: 1:1 bot DM, deep-link-bound (no groups)
| Dimension | Assessment |
|-----------|------------|
| Complexity | Low — one `chat_id ↔ client` row, set on link-tap; reuses onboarding |
| Cost | Free (Telegram); per-msg template (WhatsApp/Twilio) |
| Scalability | Excellent — 1:1 is native on both platforms |
| Platform support | Telegram + WhatsApp (Twilio 1:1 is supported; groups are not) |

**Pros:** deterministic routing; clean opt-in consent; both platforms;
chase-reply + drop in one thread.
**Cons:** client taps a link once; WhatsApp business-initiated chase needs an
approved template + 24h-window handling.

### Option C: Email-ingest address only
**Pros:** zero interaction for auto-forward; deterministic; already shipped.
**Cons:** no good chase loop (email nudges = low response).

### Option D: Hybrid — Email (passive) + 1:1 deep-link bot (chase + interactive) — **RECOMMENDED / ACCEPTED**
| Dimension | Assessment |
|-----------|------------|
| Complexity | Low–Med — both reuse existing infra |
| Cost | Mailbox + Telegram free + Twilio (drop-in-key) |
| Scalability | Excellent — both route deterministically, no groups |
| Platform support | Email + Telegram + WhatsApp |

**Pros:** covers hands-off (bank → email) *and* interactive chase/drop (1:1
bot); no group management; clean consent; fits the messaging factory + the
dormant Twilio client.
**Cons:** two channels; a small per-client `channel + binding` record.

## Trade-off Analysis

The group model trades marginal transparency for disqualifying costs: it is
**technically impossible on WhatsApp**, and on Telegram it pushes per-client
ops burden onto the CA with brittle group→client routing. Option B/D's
deep-link binding is strictly better for routing — a single authoritative
`chat_id↔client` row set at opt-in, identical to a production mechanism, and
it works on *both* platforms because 1:1 is universal. Email-only (C) lacks a
chase loop. The hybrid (D) keeps email as the frictionless passive path and
adds the 1:1 bot as the active path — auto-forward *and* a conversational
nudge, with no group anywhere.

## Consequences

**Easier:** routing is one bound row (or a UUID in an address) — never fuzzy;
WhatsApp unblocked (1:1 is API-legal); explicit DPDP-clean consent; reuses
onboarding deep-link, messaging factory, email ingest, `brain_events`.

**Harder / to revisit:** need a per-client `client_channels` binding; WhatsApp
chases need approved templates; the CA distributes each client's deep-link /
ingest address once.

## Action Items
1. [ ] `client_channels` binding (telegram chat_id / whatsapp phone / ingest
       email per client) + a client deep-link generator (extend onboarding
       `/start <uuid>` to clients).
2. [ ] Layer 2 ingestion seam (raw-bytes → sniff → Brain) so email-poll and
       1:1-bot drops land identically.
3. [ ] Filing-period-aware completeness-chase cron: per client, check
       `brain_events` for required docs; chase on the bound channel.
4. [ ] Keep Twilio WhatsApp a drop-in-key 1:1 adapter (no group code).
5. [ ] (Later) Account Aggregator for true zero-touch bank pull.
