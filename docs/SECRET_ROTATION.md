# Secret Rotation Procedure

Closes Security Checklist #19. Run this quarterly (or immediately on any
suspected compromise).

## Inventory

| Secret | Store | Rotation cadence |
|---|---|---|
| `BACKEND_API_KEY` | AWS Secrets Manager | Quarterly |
| `GATEWAY_HMAC_SECRET` | AWS Secrets Manager | Quarterly |
| `LLM_API_KEY` | AWS Secrets Manager | Quarterly (or on vendor advisory) |
| `SERPER_API_KEY` | AWS Secrets Manager | Quarterly |
| `PAGEONE_API_KEY` | AWS Secrets Manager | Quarterly |
| `SENTRY_DSN` | AWS Secrets Manager | On demand (after Sentry project re-key) |
| `TELEGRAM_BOT_TOKEN` | Encrypted column `ca_firms.telegram_bot_token` | On demand (firm request, suspected leak) |
| `DATABASE_URL` password | Supabase dashboard | Quarterly |

## Standard procedure (inter-service auth — BACKEND_API_KEY / GATEWAY_HMAC_SECRET)

Both services must agree on these values at the same instant — but the
gateway dispatches with a 5-minute replay window, so a brief overlap is
safe.

1. Generate a new value:
   ```sh
   openssl rand -hex 32
   ```
2. Stage the new value alongside the old one in AWS Secrets Manager
   under a `_NEXT` suffix (`BACKEND_API_KEY_NEXT`).
3. Deploy backend with code that accepts either current or `_NEXT`
   (the dual-accept window is the `accepts_legacy_secrets` feature flag).
4. Once backend rollout is verified, deploy gateway with `_NEXT` as its
   active value.
5. Promote `_NEXT` to current in Secrets Manager, drop the old key.
6. Disable the `accepts_legacy_secrets` flag on the backend; next deploy
   removes the dual-accept path.

Total rollover window: ~30 minutes.

## LLM API key

OpenAI-compatible keys can be rotated in place — the LLM client reads the
secret on every call.

1. Generate new key in the vendor console.
2. Update `LLM_API_KEY` in Secrets Manager.
3. Trigger a backend rolling restart (`aws ecs update-service --force-new-deployment`).
4. Revoke the old key in the vendor console **only after** the deploy
   is healthy in CloudWatch.

## Telegram bot token

Per-firm secret. Rotating breaks the firm's bot temporarily — schedule
a window with the firm admin.

1. Get the new token from [@BotFather](https://t.me/BotFather)
   (`/revoke` then `/token`).
2. Update the encrypted column:
   ```sql
   UPDATE ca_firms
   SET telegram_bot_token = '<new token>'
   WHERE id = '<firm id>';
   ```
3. Restart the gateway pod responsible for that firm
   (sticky-assigned by firm id).
4. Confirm the bot answers to `/start` from the firm admin.

## Database password

1. Rotate in the Supabase dashboard.
2. Update `DATABASE_URL` in Secrets Manager (full DSN, including
   `sslmode=require`).
3. Roll the backend. The connection pool tears down old connections on
   pod stop.

## Audit trail

After every rotation, append to `docs/rotation_log.md`:

```
2026-Q2 — BACKEND_API_KEY rotated by sarthak@enma.in. Old key revoked 2026-06-10T11:30Z.
```

Keep the log in git. It's the evidence for the audit that #19 is being
honoured.
