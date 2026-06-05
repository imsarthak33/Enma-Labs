// =============================================================================
// Structured JSON logger.
//
// One log line = one JSON object on stdout. Levels filtered by config.logLevel.
// `console.*` is permitted here (this is the structured-output boundary) and
// banned everywhere else by the eslint config.
// =============================================================================

import { config } from "../config.js";

const LEVELS = { debug: 10, info: 20, warn: 30, error: 40 };

/**
 * @param {string} level
 * @returns {boolean}
 */
function shouldEmit(level) {
  return LEVELS[level] >= LEVELS[config.logLevel];
}

/**
 * @param {string} level
 * @param {string} message
 * @param {Record<string, unknown> | undefined} fields
 */
function emit(level, message, fields) {
  if (!shouldEmit(level)) return;
  const entry = {
    ts: new Date().toISOString(),
    level,
    service: config.service.name,
    msg: message,
    ...(fields || {}),
  };
  // stdout for info/debug, stderr for warn/error — keeps container log
  // routers separating signal from noise.
  const line = JSON.stringify(entry);
  if (level === "warn" || level === "error") {
    console.error(line);
  } else {
    console.log(line);
  }
}

export const logger = {
  /**
   * @param {string} message
   * @param {Record<string, unknown>} [fields]
   */
  debug: (message, fields) => emit("debug", message, fields),
  info: (message, fields) => emit("info", message, fields),
  warn: (message, fields) => emit("warn", message, fields),
  error: (message, fields) => emit("error", message, fields),
};

/**
 * Returns a logger that pre-fills certain fields (e.g., request_id).
 * @param {Record<string, unknown>} bound
 */
export function withContext(bound) {
  return {
    debug: (m, f) => emit("debug", m, { ...bound, ...(f || {}) }),
    info: (m, f) => emit("info", m, { ...bound, ...(f || {}) }),
    warn: (m, f) => emit("warn", m, { ...bound, ...(f || {}) }),
    error: (m, f) => emit("error", m, { ...bound, ...(f || {}) }),
  };
}
