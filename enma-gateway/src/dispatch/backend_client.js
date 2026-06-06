// =============================================================================
// Backend dispatch — the single egress point from gateway to backend.
//
// Contract:
//   • Fire-and-forget: we send and stop caring. The backend ACKs the user
//     via its own Telegram client; gateway never relays a backend response
//     back to the user.
//   • 5s connection timeout: a wedged backend must not stall the poller.
//   • Each envelope is HMAC-signed *before* this module is called. We add
//     the API key header for additional defence-in-depth.
//
// We deliberately reject 4xx/5xx loudly in logs so they show up in Sentry
// during Phase 8, but we don't retry — the poller will re-deliver the same
// update on the next loop iteration if Telegram hasn't been ACKed yet.
//
// Concurrency cap (Phase 8 — rate limiting)
// ------------------------------------------
// Without a cap, a media-group burst (Telegram delivers all N photos at
// once) or a cron fan-out can fire 100+ simultaneous HTTP calls to the
// backend, potentially overwhelming it. We use a simple semaphore
// (DISPATCH_CONCURRENCY) to cap the number of concurrent in-flight
// requests. Callers queue behind the semaphore rather than failing;
// because the gateway is fire-and-forget this adds latency without
// dropping messages.
//
// Default: 20 concurrent dispatches. Override via DISPATCH_CONCURRENCY
// env var. Setting it to 0 disables the cap (test / high-throughput mode).
// =============================================================================

import { config } from "../config.js";
import { logger, withContext } from "../utils/logger.js";

/** @type {Readonly<Record<string, string>>} */
export const ROUTE_BY_KIND = Object.freeze({
  document: "/worker/document",
  document_batch: "/worker/document-batch",
  command: "/worker/command",
  voice: "/worker/voice",
  callback: "/worker/callback",
  // Phase 7 cron envelopes — ADR-007 §Decision 3.
  cron_task_heartbeat: "/worker/cron/task-heartbeat",
  cron_morning_briefing: "/worker/cron/morning-briefing",
  cron_client_chase: "/worker/cron/client-chase",
});

const DEFAULT_TIMEOUT_MS = 5000;

// ---------------------------------------------------------------------------
// Concurrency semaphore
// ---------------------------------------------------------------------------

/**
 * Default max concurrent outbound dispatch calls.
 * 0 = unlimited (useful for isolated unit tests).
 */
export const DISPATCH_CONCURRENCY =
  process.env.DISPATCH_CONCURRENCY !== undefined
    ? Math.max(0, Number.parseInt(process.env.DISPATCH_CONCURRENCY, 10) || 0)
    : 20;

/**
 * A minimal promise-based semaphore.
 * Callers call ``acquire()`` to wait for a slot and ``release()`` when done.
 */
export class Semaphore {
  /** @param {number} limit  0 = unlimited */
  constructor(limit) {
    this._limit = limit;
    this._count = 0;
    /** @type {Array<() => void>} */
    this._queue = [];
  }

  /** @returns {Promise<void>} */
  acquire() {
    if (this._limit === 0 || this._count < this._limit) {
      this._count++;
      return Promise.resolve();
    }
    return new Promise((resolve) => this._queue.push(resolve));
  }

  release() {
    this._count--;
    if (this._queue.length > 0) {
      this._count++;
      const next = this._queue.shift();
      if (next) next();
    }
  }
}

/** Process-wide semaphore for backend dispatch. */
export const dispatchSemaphore = new Semaphore(DISPATCH_CONCURRENCY);

/**
 * @param {string} kind
 * @returns {string}
 */
export function routeForKind(kind) {
  const route = ROUTE_BY_KIND[kind];
  if (!route) throw new Error(`no backend route for envelope kind: ${kind}`);
  return route;
}

/**
 * @param {string} base
 * @param {string} path
 */
function joinUrl(base, path) {
  const trimmedBase = base.endsWith("/") ? base.slice(0, -1) : base;
  const prefixedPath = path.startsWith("/") ? path : `/${path}`;
  return `${trimmedBase}${prefixedPath}`;
}

/**
 * @typedef {object} OuterEnvelope
 * @property {number} v
 * @property {string} kind
 * @property {string} payload_b64
 * @property {string} signature
 */

/**
 * @typedef {object} DispatchOptions
 * @property {typeof fetch} [fetchImpl]
 * @property {number} [timeoutMs]
 * @property {string} [backendUrl]
 * @property {string} [apiKey]
 * @property {string} [requestId]
 * @property {Semaphore} [semaphore]   override for tests (pass new Semaphore(0) for unlimited)
 */

/**
 * Dispatch an envelope to the backend. Resolves once the response status is
 * known; the caller decides whether to await or to fire-and-forget.
 *
 * Acquires a slot from :data:`dispatchSemaphore` before making the HTTP call
 * so burst traffic is queued rather than dropped.
 *
 * @param {OuterEnvelope} outer
 * @param {DispatchOptions} [opts]
 * @returns {Promise<{ ok: boolean, status: number }>}
 */
export async function dispatchEnvelope(outer, opts = {}) {
  const fetchImpl = opts.fetchImpl ?? fetch;
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const backendUrl = opts.backendUrl ?? config.backend.url;
  const apiKey = opts.apiKey ?? config.backend.apiKey;
  const semaphore = opts.semaphore ?? dispatchSemaphore;

  const route = routeForKind(outer.kind);
  const url = joinUrl(backendUrl, route);
  const log = withContext({
    request_id: opts.requestId,
    kind: outer.kind,
    route,
  });

  // Wait for a semaphore slot (no-op when limit=0).
  await semaphore.acquire();

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  const headers = {
    "Content-Type": "application/json",
    "X-Enma-Api-Key": apiKey,
    "X-Enma-Signature": outer.signature,
    "X-Enma-Envelope-Version": String(outer.v),
  };

  try {
    const res = await fetchImpl(url, {
      method: "POST",
      headers,
      body: JSON.stringify(outer),
      signal: controller.signal,
    });
    if (!res.ok) {
      log.error("backend_dispatch_non_2xx", { status: res.status });
    } else {
      log.debug("backend_dispatch_ok", { status: res.status });
    }
    return { ok: res.ok, status: res.status };
  } catch (err) {
    const aborted = /** @type {Error & { name?: string }} */ (err).name === "AbortError";
    log.error("backend_dispatch_failed", {
      error: String(err),
      aborted,
      timeout_ms: timeoutMs,
    });
    return { ok: false, status: 0 };
  } finally {
    clearTimeout(timer);
    semaphore.release();
  }
}

/**
 * Fire-and-forget wrapper: callers that explicitly do not want to await the
 * round-trip can use this. Errors are still logged via dispatchEnvelope.
 * @param {OuterEnvelope} outer
 * @param {DispatchOptions} [opts]
 */
export function dispatchEnvelopeAsync(outer, opts) {
  void dispatchEnvelope(outer, opts).catch((err) => {
    // Defence-in-depth: dispatchEnvelope already catches, but if something
    // throws synchronously before the try block we still don't want an
    // unhandledRejection bringing the process down.
    logger.error("backend_dispatch_unhandled", { error: String(err) });
  });
}
