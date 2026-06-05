// =============================================================================
// Telegram long-polling — the gateway's ingress.
//
// Calls the Telegram Bot API's getUpdates with a 30-second long-poll timeout.
// Each update is deduped via DedupCache then routed via message_router.
//
// Lifecycle:
//   1. startPoller(routeFn) — begins polling in a perpetual async loop.
//   2. The returned stop() function aborts the current fetch and exits the loop.
//   3. On transient errors (network, 5xx), backs off exponentially up to 30s.
//
// This module has zero knowledge of envelopes, handlers, or buffering. It
// only knows how to pull updates and call a route function for each one.
// =============================================================================

import { config } from "../config.js";
import { logger } from "../utils/logger.js";
import { DedupCache } from "./dedup_cache.js";

const TELEGRAM_API = "https://api.telegram.org";
const POLL_TIMEOUT_S = 30;
const MAX_BACKOFF_MS = 30_000;
const ALLOWED_UPDATES = ["message", "callback_query"];

/**
 * @typedef {object} PollerOptions
 * @property {(update: object) => void} routeFn           Required: called for each deduped update
 * @property {typeof globalThis.fetch}  [fetchImpl]       Test seam
 * @property {string}                   [botToken]        Override bot token
 * @property {number}                   [pollTimeoutS]    Long-poll timeout in seconds
 * @property {number}                   [dedupCapacity]   DedupCache capacity
 */

/**
 * Start the Telegram long-polling loop.
 *
 * @param {PollerOptions} opts
 * @returns {{ stop: () => Promise<void> }}
 */
export function startPoller(opts) {
  const {
    routeFn,
    fetchImpl = globalThis.fetch,
    botToken = config.telegram.botToken,
    pollTimeoutS = POLL_TIMEOUT_S,
    dedupCapacity = 1000,
  } = opts;

  if (typeof routeFn !== "function") {
    throw new Error("startPoller requires a routeFn");
  }

  const dedup = new DedupCache(dedupCapacity);
  const controller = new AbortController();
  let offset = 0;
  let running = true;
  let backoffMs = 500;

  const baseUrl = `${TELEGRAM_API}/bot${botToken}/getUpdates`;

  async function poll() {
    logger.info("poller_started", { poll_timeout_s: pollTimeoutS });

    while (running) {
      try {
        const url = new URL(baseUrl);
        url.searchParams.set("offset", String(offset));
        url.searchParams.set("timeout", String(pollTimeoutS));
        url.searchParams.set("allowed_updates", JSON.stringify(ALLOWED_UPDATES));

        const res = await fetchImpl(url.toString(), {
          method: "GET",
          signal: controller.signal,
        });

        if (!res.ok) {
          logger.error("telegram_api_error", { status: res.status });
          await sleep(backoffMs);
          backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
          continue;
        }

        const body = await res.json();
        if (!body.ok || !Array.isArray(body.result)) {
          logger.error("telegram_bad_response", { body: JSON.stringify(body).slice(0, 200) });
          await sleep(backoffMs);
          backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
          continue;
        }

        // Reset backoff on success.
        backoffMs = 500;

        const updates = body.result;
        if (updates.length === 0) continue;

        for (const update of updates) {
          // Advance offset past this update.
          if (update.update_id >= offset) {
            offset = update.update_id + 1;
          }

          // Dedup check.
          if (dedup.seen(update.update_id)) {
            logger.debug("update_deduped", { update_id: update.update_id });
            continue;
          }

          // Route the update.
          try {
            routeFn(update);
          } catch (err) {
            logger.error("route_update_error", {
              update_id: update.update_id,
              error: String(err),
              stack: /** @type {Error} */ (err).stack,
            });
          }
        }
      } catch (err) {
        if (/** @type {Error} */ (err).name === "AbortError") {
          logger.info("poller_aborted");
          break;
        }
        logger.error("poller_error", {
          error: String(err),
          backoff_ms: backoffMs,
        });
        await sleep(backoffMs);
        backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
      }
    }

    logger.info("poller_stopped");
  }

  // Fire-and-forget the loop.
  const loopPromise = poll();

  const stop = async () => {
    running = false;
    controller.abort();
    await loopPromise;
  };

  return { stop };
}

/**
 * @param {number} ms
 * @returns {Promise<void>}
 */
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
