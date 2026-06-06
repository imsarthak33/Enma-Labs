# Firm Onboarding Playbook

Bringing a new CA firm onto Enma. End-to-end: provision the firm row, wire
the Telegram bot, seed the first clients, and validate with a smoke pass.

## Prerequisites

* Backend and gateway deployed (see [DEPLOYMENT.md](DEPLOYMENT.md)).
* Migrations through revision `004` applied.
* Operator account with the platform-owner role on the database.

## 1. Provision the firm row

```sql
-- Run as platform owner. The bot_token is encrypted at rest by the column
-- trigger — pass it in plaintext, it never lands in storage in cleartext.
INSERT INTO ca_firms (
    id, name, admin_chat_id, telegram_bot_token, is_active
) VALUES (
    uuid_generate_v4(),
    '<firm display name>',
    <admin chat id>,
    '<bot token from BotFather>',
    true
)
RETURNING id;
```

Note the returned `id` — every subsequent insert uses it.

## 2. Configure the Telegram bot

1. Talk to [@BotFather](https://t.me/BotFather), create the bot, copy the
   token into step 1.
2. Disable "Group Privacy" so the bot can see all messages in groups.
3. Set the description: "Your firm's autonomous compliance copilot."
4. Set commands:
   ```
   add_client - Register a new client
   list_clients - Show all active clients
   status - Show client document/filing status
   assign - Assign pending document to a client
   ```
5. In the operator dashboard (or via psql), assign the firm admin Telegram
   user id to the firm row's `admin_chat_id` column.

## 3. Seed pilot clients

```sql
INSERT INTO clients (ca_firm_id, trade_name, gstin, is_active)
VALUES
    ('<firm-id>', 'ABC Corp Pvt Ltd',  '27ABCDE1234F1Z5', true),
    ('<firm-id>', 'XYZ Traders',       '07ZYXWV4567G3K2', true),
    ('<firm-id>', 'Acme Logistics',    '29ACMEL9999H1J7', true),
    ('<firm-id>', 'Sunrise Foods',     '06SUNRI8888K2L8', true),
    ('<firm-id>', 'Nimbus Cloud LLP',  '24NIMBU7777M5N3', true);
```

Five pilots is the spec target. Fewer is fine for staged rollout; more is
not — keep the first cohort small enough to call the firm admin if
anything goes wrong.

## 4. Smoke pass

Have the firm admin do each of these from Telegram:

1. **Single-photo upload** of a GST invoice for `ABC Corp`.
   Expected: ack within 2 s, extraction message with vendor, totals, ITC.
2. **Multi-photo upload** of a 3-page document, same `media_group_id`.
   Expected: single batched extraction within 5 s.
3. **Text command:** `What's the ITC status for ABC Corp?`
   Expected: supervisor reply summarising claimable ITC for the period.
4. **Voice note:** "Show me pending tasks."
   Expected: transcription preview + the same supervisor reply.
5. **Filing approval:** `ENMA APPROVE FILING March 2026`
   Expected: confirm-step ack, then a locked-period audit row.

If any step fails, capture the request id from the Sentry breadcrumb and
pivot in the dashboard before retrying.

## 5. Operational checklist

- [ ] Sentry project for this firm has the production DSN configured.
- [ ] Grafana data source row created for the firm (read-only role).
- [ ] First morning briefing at 09:00 IST the next day delivered to the
      firm admin.
- [ ] 28th-of-month client chase verified by inspecting
      `client_notifications` after the next 28th.
- [ ] Idempotency log size plateaued (`SELECT * FROM idempotency_log_size`)
      — proves the daily cleanup cron is firing.

## Rollback

If onboarding has to be reversed in the first 24 h:

```sql
UPDATE ca_firms SET is_active = false WHERE id = '<firm-id>';
UPDATE clients  SET is_active = false WHERE ca_firm_id = '<firm-id>';
```

The bot will stop responding (gateway honours `is_active`); historical
data is preserved for audit. Do **not** delete the rows — RLS depends on
firm rows existing for any cross-tenant audit trail to remain intact.
