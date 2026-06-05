# enma-gateway

Node.js 20 service that owns Telegram I/O for Enma Labs. Zero business logic, zero database access, zero LLM calls — it polls, dedups, buffers, signs envelopes, and dispatches to the Python backend.

## Stack

- Node.js 20 LTS (ESM modules)
- Native `fetch`, `crypto`, `http` — no Express/Fastify on the hot path
- `node-cron` for scheduled jobs (Phase 7)
- `dotenv` for local env loading
- Vitest for unit tests, ESLint 9 + Prettier 3 for hygiene

## Layout (Phase 0 baseline)

```
enma-gateway/
├── package.json
├── Dockerfile
├── .env.example
├── eslint.config.js
├── vitest.config.js
├── src/
│   ├── index.js               # Entry: starts HTTP listener + (later) poller
│   ├── config.js              # Env loader + validator
│   └── utils/
│       ├── logger.js          # Structured JSON logger
│       └── health.js          # /health response builder
└── tests/
    ├── config.test.js
    └── logger.test.js
```

Later phases append:
- `src/polling/` — Telegram long-poll loop (Phase 2)
- `src/routing/` — message → handler routing (Phase 2)
- `src/buffering/media_group_buffer.js` — 3000ms media group window (Phase 2)
- `src/dispatch/backend_client.js` — HMAC-signed POSTs to backend (Phase 2)
- `src/cron/` — node-cron schedules (Phase 7)

## Local development

```bash
# From repo root:
docker compose up --build gateway

# Or standalone:
npm install
cp .env.example .env
npm run dev
```

Health check:

```bash
curl http://localhost:3000/health
# {"status":"ok","service":"enma-gateway","version":"0.1.0","uptime_s":3.2,"env":"development"}
```

## Quality gates

```bash
npm run lint
npm run format:check
npm run test:coverage
```

## Ground rules

1. **Zero business logic.** All decisions belong to the backend.
2. **Fire-and-forget dispatch.** Backend ack arrives at Telegram via the backend's own client, not via gateway response.
3. **HMAC every envelope.** Phase 2 wires this in; the secret is loaded from `GATEWAY_HMAC_SECRET`.
