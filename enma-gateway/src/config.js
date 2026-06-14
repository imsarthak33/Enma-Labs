// =============================================================================
// enma-gateway — centralised environment configuration.
//
// One module. One singleton. All other modules import { config } from here.
// Validation fails fast on boot so misconfiguration cannot reach runtime.
// =============================================================================

import "dotenv/config";

const VALID_ENVS = ["development", "staging", "production", "test"];
const VALID_LEVELS = ["debug", "info", "warn", "error"];
// First entry is the default when LOG_FORMAT is unset.
const VALID_LOG_FORMATS = ["auto", "json", "console"];

/**
 * @param {string} name
 * @param {string | undefined} value
 * @returns {string}
 */
function required(name, value) {
  if (value === undefined || value === null || value === "") {
    throw new Error(`Missing required env var: ${name}`);
  }
  return value;
}

/**
 * @param {string} name
 * @param {string | undefined} value
 * @param {string[]} allowed
 * @returns {string}
 */
function oneOf(name, value, allowed) {
  if (value === undefined) return allowed[0];
  if (!allowed.includes(value)) {
    throw new Error(`${name} must be one of ${allowed.join(", ")}; got "${value}"`);
  }
  return value;
}

/**
 * @param {string} name
 * @param {string | undefined} raw
 * @param {{ min?: number; max?: number; default: number }} opts
 */
function asInt(name, raw, opts) {
  if (raw === undefined || raw === "") return opts.default;
  const n = Number.parseInt(raw, 10);
  if (!Number.isFinite(n)) {
    throw new Error(`${name} must be an integer; got "${raw}"`);
  }
  if (opts.min !== undefined && n < opts.min) {
    throw new Error(`${name} must be >= ${opts.min}; got ${n}`);
  }
  if (opts.max !== undefined && n > opts.max) {
    throw new Error(`${name} must be <= ${opts.max}; got ${n}`);
  }
  return n;
}

const env = oneOf("NODE_ENV", process.env.NODE_ENV, VALID_ENVS);
const isProduction = env === "production";

/** @typedef {Readonly<ReturnType<typeof build>>} GatewayConfig */
function build() {
  // In non-production, allow weak placeholders so `docker compose up` works
  // even without a fully-populated .env. Production must be explicit.
  const placeholder = (val, fallback) =>
    isProduction ? required(val.name, val.value) : val.value || fallback;

  const cfg = Object.freeze({
    env,
    isProduction,
    logLevel: oneOf("LOG_LEVEL", process.env.LOG_LEVEL, VALID_LEVELS),
    logFormat: oneOf("LOG_FORMAT", process.env.LOG_FORMAT, VALID_LOG_FORMATS),
    port: asInt("PORT", process.env.PORT, { min: 0, max: 65535, default: 3000 }),

    telegram: Object.freeze({
      botToken: placeholder(
        { name: "TELEGRAM_BOT_TOKEN", value: process.env.TELEGRAM_BOT_TOKEN },
        "changeme-telegram-bot-token",
      ),
    }),

    backend: Object.freeze({
      url: placeholder(
        { name: "BACKEND_URL", value: process.env.BACKEND_URL },
        "http://localhost:8000",
      ),
      apiKey: placeholder(
        { name: "BACKEND_API_KEY", value: process.env.BACKEND_API_KEY },
        "dev_backend_api_key_change_me",
      ),
      hmacSecret: placeholder(
        { name: "GATEWAY_HMAC_SECRET", value: process.env.GATEWAY_HMAC_SECRET },
        "dev_hmac_secret_change_me",
      ),
    }),

    observability: Object.freeze({
      sentryDsn: process.env.SENTRY_DSN || null,
    }),

    service: Object.freeze({
      name: "enma-gateway",
      version: "0.1.0",
    }),
  });

  return cfg;
}

export const config = build();

/**
 * Returns a copy of the config safe for logging (secrets redacted).
 * @returns {Record<string, unknown>}
 */
export function safeConfigSnapshot() {
  return {
    env: config.env,
    log_level: config.logLevel,
    port: config.port,
    backend_url: config.backend.url,
    backend_api_key: config.backend.apiKey ? "***" : "",
    gateway_hmac_secret: config.backend.hmacSecret ? "***" : "",
    telegram_bot_token: config.telegram.botToken ? "***" : "",
    sentry_dsn_configured: Boolean(config.observability.sentryDsn),
    service: config.service.name,
    version: config.service.version,
  };
}
