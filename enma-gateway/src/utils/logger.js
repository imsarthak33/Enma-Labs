// =============================================================================
// Structured logger — JSON in prod, human-readable text in dev.
//
// Levels filtered by config.logLevel. Format chosen by config.logFormat:
//   * "json"    — single-line JSON per record (CloudWatch / Sentry parse)
//   * "console" — dev-friendly text:
//                 12:34:56.789 [info ] message field=value field=value
//   * "auto"    — json when NODE_ENV=production, console otherwise
//
// `console.*` is permitted here (this is the structured-output boundary)
// and banned everywhere else by the eslint config.
// =============================================================================

import { config } from "../config.js";

const LEVELS = { debug: 10, info: 20, warn: 30, error: 40 };

/**
 * Resolve "auto" to the concrete renderer name once at module load.
 */
const RESOLVED_FORMAT =
  config.logFormat === "auto" ? (config.isProduction ? "json" : "console") : config.logFormat;

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
function renderJson(level, message, fields) {
  const entry = {
    ts: new Date().toISOString(),
    level,
    service: config.service.name,
    msg: message,
    ...(fields || {}),
  };
  return JSON.stringify(entry);
}

/**
 * @param {string} level
 * @param {string} message
 * @param {Record<string, unknown> | undefined} fields
 */
function renderConsole(level, message, fields) {
  // 12:34:56.789 [info ] message_name field=value field=value
  const now = new Date();
  const ts = now.toISOString().slice(11, 23); // HH:MM:SS.mmm
  const pad = level.padEnd(5);
  const tail = fields
    ? Object.entries(fields)
        .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
        .join(" ")
    : "";
  return tail ? `${ts} [${pad}] ${message} ${tail}` : `${ts} [${pad}] ${message}`;
}

/**
 * @param {string} level
 * @param {string} message
 * @param {Record<string, unknown> | undefined} fields
 */
function emit(level, message, fields) {
  if (!shouldEmit(level)) return;
  const line = RESOLVED_FORMAT === "json" ? renderJson(level, message, fields) : renderConsole(level, message, fields);
  // stdout for info/debug, stderr for warn/error — keeps container log
  // routers separating signal from noise.
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
