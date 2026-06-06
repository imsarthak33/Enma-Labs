// =============================================================================
// enma-gateway — Sentry initialisation and PII scrubbing.
//
// initSentry() is a no-op when SENTRY_DSN is not configured, so dev and tests
// run unchanged. In staging/production the DSN is required and a beforeSend
// hook strips request bodies, headers, breadcrumb data, and the user object
// before transmission.
//
// Scrub rules mirror the backend (app/utils/masking.py) so both services
// behave identically when an event leaves the trust boundary.
// =============================================================================

import * as Sentry from "@sentry/node";

import { config } from "../config.js";

const SENSITIVE_KEY_FRAGMENTS = [
  "telegram_bot_token",
  "bot_token",
  "api_key",
  "apikey",
  "secret",
  "password",
  "passwd",
  "token",
  "authorization",
  "database_url",
  "dsn",
  "encryption_key",
  "hmac_secret",
  "pan",
  "gstin",
  "aadhaar",
  "bank_account",
  "account_number",
  "ifsc",
];

const MASK = "***";
const HEAD_KEEP = 4;

function isSensitive(key) {
  if (typeof key !== "string") return false;
  const lowered = key.toLowerCase();
  return SENSITIVE_KEY_FRAGMENTS.some((frag) => lowered.includes(frag));
}

function maskValue(value) {
  if (value === null || value === undefined) return value;
  if (typeof value === "string") {
    return value.length > HEAD_KEEP ? `${value.slice(0, HEAD_KEEP)}${MASK}` : MASK;
  }
  return MASK;
}

/**
 * Deep-clone with sensitive keys masked. Recurses into nested objects and
 * homogeneous arrays of objects. Non-sensitive arrays pass through unchanged.
 * @param {unknown} data
 * @returns {unknown}
 */
export function maskObject(data) {
  if (data === null || data === undefined) return data;
  if (Array.isArray(data)) {
    return data.map((item) => (item && typeof item === "object" ? maskObject(item) : item));
  }
  if (typeof data !== "object") return data;

  /** @type {Record<string, unknown>} */
  const out = {};
  for (const [key, value] of Object.entries(data)) {
    if (isSensitive(key)) {
      out[key] = maskValue(value);
      continue;
    }
    if (value && typeof value === "object") {
      out[key] = maskObject(value);
      continue;
    }
    out[key] = value;
  }
  return out;
}

/**
 * Sentry beforeSend hook. Returns the scrubbed event, never null —
 * we want error visibility, just not the payloads.
 * @param {Sentry.ErrorEvent} event
 * @returns {Sentry.ErrorEvent}
 */
export function scrubEvent(event) {
  if (event.request) {
    if ("data" in event.request) {
      event.request.data = "[REDACTED]";
    }
    if (event.request.headers) {
      event.request.headers = /** @type {Record<string,string>} */ (
        maskObject(event.request.headers)
      );
    }
    if (event.request.cookies) {
      event.request.cookies = /** @type {Record<string,string>} */ (
        maskObject(event.request.cookies)
      );
    }
  }

  if (event.extra) {
    event.extra = /** @type {Record<string, unknown>} */ (maskObject(event.extra));
  }
  if (event.tags) {
    event.tags = /** @type {Record<string, string | number | boolean | null>} */ (
      maskObject(event.tags)
    );
  }

  if (event.breadcrumbs) {
    event.breadcrumbs = event.breadcrumbs.map((crumb) => {
      if (crumb.data) {
        return { ...crumb, data: /** @type {Record<string, unknown>} */ (maskObject(crumb.data)) };
      }
      return crumb;
    });
  }

  if (event.user) {
    event.user = event.user.id ? { id: event.user.id } : {};
  }

  return event;
}

let initialised = false;

/**
 * Idempotent initialiser. Safe to call from main() and from tests.
 * Returns true when Sentry was actually initialised.
 * @returns {boolean}
 */
export function initSentry() {
  if (initialised) return true;
  const dsn = config.observability.sentryDsn;
  if (!dsn) return false;

  Sentry.init({
    dsn,
    environment: config.env,
    release: `${config.service.name}@${config.service.version}`,
    tracesSampleRate: 0.1,
    sendDefaultPii: false,
    beforeSend: scrubEvent,
  });
  initialised = true;
  return true;
}

/**
 * Test-only reset so suites can re-init with different DSNs.
 * Not part of the production surface.
 */
export function _resetForTests() {
  initialised = false;
}
