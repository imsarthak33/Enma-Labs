// =============================================================================
// Health probe payload builder.
//
// Liveness only — gateway does not own any downstream that should fail
// readiness. (The Telegram long-poll loop reconnects on its own.)
// =============================================================================

import { config } from "../config.js";

const BOOT_MONO_S = process.hrtime.bigint();

/**
 * @returns {{
 *   status: "ok",
 *   service: string,
 *   version: string,
 *   uptime_s: number,
 *   env: string,
 * }}
 */
export function buildHealthPayload() {
  const nowNs = process.hrtime.bigint();
  const uptimeS = Number(nowNs - BOOT_MONO_S) / 1e9;
  return {
    status: "ok",
    service: config.service.name,
    version: config.service.version,
    uptime_s: Math.round(uptimeS * 1000) / 1000,
    env: config.env,
  };
}
