// =============================================================================
// Phase 7 — gateway-side cron scheduler.
//
// ADR-007 §Decision 1: the wall clock lives in the gateway, not the backend.
// We register three node-cron jobs in the IST timezone; each fires an
// HMAC-signed envelope to the matching /worker/cron/* endpoint.
//
// The envelopes have:
//   chat_id    = null  (cron is not user-targeted; backend fans out per firm)
//   message_id = null  (no Telegram message exists)
//   payload    = { scheduled_at: <ISO-8601 UTC> }
//
// The backend's idempotency middleware derives a synthetic
// (chat_id=0, message_id=floor(scheduled_at/60)) key for the dedup table so a
// double-fire (e.g. a manual trigger that races the cron) is a safe no-op.
// =============================================================================

import cron from "node-cron";

import { config } from "../config.js";
import { dispatchEnvelopeAsync } from "../dispatch/backend_client.js";
import { buildEnvelope, CRON_ENVELOPE_KINDS } from "../utils/envelope.js";
import { logger } from "../utils/logger.js";

const IST = "Asia/Kolkata";

/**
 * @typedef {object} CronJobSpec
 * @property {string} kind        envelope kind / route discriminator
 * @property {string} schedule    cron expression (parsed in IST)
 * @property {string} description human-readable purpose, for logs
 */

/** @type {Readonly<CronJobSpec[]>} */
export const CRON_JOBS = Object.freeze([
  {
    kind: "cron_task_heartbeat",
    schedule: "*/5 * * * *",
    description: "Notify overdue tasks (5-minute cadence).",
  },
  {
    kind: "cron_morning_briefing",
    schedule: "30 3 * * 1-6",
    description: "Send the 09:00 IST morning brief (Mon-Sat).",
  },
  {
    kind: "cron_client_chase",
    schedule: "30 3 28 * *",
    description: "Chase clients with missing docs on the 28th, 09:00 IST.",
  },
  {
    // Spec §2.4 — daily prune of idempotency_log entries older than 72 h.
    // Runs at 02:00 IST (20:30 UTC the previous day) when traffic is lowest.
    kind: "cron_idempotency_cleanup",
    schedule: "0 2 * * *",
    description: "Daily prune of idempotency_log (72 h TTL).",
  },
]);

/**
 * Fire one cron envelope. Exported so tests can call it directly.
 *
 * @param {string} kind
 * @param {Date} [now]
 */
export function fireCronEnvelope(kind, now = new Date()) {
  if (!CRON_ENVELOPE_KINDS.includes(kind)) {
    throw new Error(`fireCronEnvelope: not a cron kind: ${kind}`);
  }
  const { outer } = buildEnvelope({
    kind,
    chatId: null,
    messageId: null,
    updateId: null,
    payload: { scheduled_at: now.toISOString() },
    secret: config.backend.hmacSecret,
  });
  logger.info("cron_fired", { kind, scheduled_at: outer ? new Date(now).toISOString() : null });
  dispatchEnvelopeAsync(outer);
}

/**
 * Register every job in :data:`CRON_JOBS`. Returns a stopper that cancels
 * them all (used by the shutdown handler).
 *
 * @param {object} [opts]
 * @param {(kind: string, now?: Date) => void} [opts.fire]   override for tests
 * @param {typeof cron} [opts.cronImpl]                       override for tests
 * @returns {{ stop: () => void, tasks: import("node-cron").ScheduledTask[] }}
 */
export function startCronScheduler({ fire = fireCronEnvelope, cronImpl = cron } = {}) {
  /** @type {import("node-cron").ScheduledTask[]} */
  const tasks = [];
  for (const job of CRON_JOBS) {
    if (!cronImpl.validate(job.schedule)) {
      throw new Error(`invalid cron expression for ${job.kind}: ${job.schedule}`);
    }
    const task = cronImpl.schedule(
      job.schedule,
      () => {
        try {
          fire(job.kind);
        } catch (err) {
          logger.error("cron_fire_failed", { kind: job.kind, error: String(err) });
        }
      },
      { scheduled: true, timezone: IST },
    );
    tasks.push(task);
    logger.info("cron_job_registered", {
      kind: job.kind,
      schedule: job.schedule,
      tz: IST,
      description: job.description,
    });
  }
  return {
    tasks,
    stop() {
      for (const t of tasks) {
        try {
          t.stop();
        } catch (err) {
          logger.warn("cron_stop_failed", { error: String(err) });
        }
      }
    },
  };
}
