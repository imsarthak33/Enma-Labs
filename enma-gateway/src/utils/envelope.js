// =============================================================================
// Inter-service envelope: how the gateway hands raw Telegram events to the
// Python backend.
//
// Wire shape (POST body):
//   {
//     "v": 1,
//     "kind": "document" | "document_batch" | "command" | "voice" | "callback",
//     "payload_b64": "<base64-encoded JSON>",
//     "signature": "<hex sha256 hmac of payload_b64>"
//   }
//
// Decoding `payload_b64` yields the inner envelope:
//   {
//     "v": 1,
//     "kind": ...,
//     "issued_at": "2026-06-05T09:30:00.000Z",
//     "nonce": "<uuid>",
//     "chat_id": <int>,
//     "message_id": <int> | null,
//     "update_id": <int> | null,
//     "payload": { ... handler-specific data ... }
//   }
//
// Why base64-then-sign instead of signing the JSON directly?
//   • The signed bytes are unambiguous — no JSON canonicalisation problems.
//   • The backend can verify before parsing, eliminating an attack surface
//     where malformed JSON forces an error path before HMAC check.
//
// The backend recomputes HMAC(secret, payload_b64) and compares with timing-
// safe equality. Idempotency is keyed on (chat_id, message_id) from the
// decoded inner envelope.
// =============================================================================

import { createHmac, randomUUID } from "node:crypto";

export const ENVELOPE_VERSION = 1;

export const ENVELOPE_KINDS = Object.freeze([
  "document",
  "document_batch",
  "command",
  "voice",
  "callback",
  // Phase 7 — gateway-initiated cron envelopes (ADR-007).
  "cron_task_heartbeat",
  "cron_morning_briefing",
  "cron_client_chase",
  // Phase 8 — daily idempotency_log prune (spec §2.4).
  "cron_idempotency_cleanup",
  // Track-A automation Phase 1 — monthly reconciliation digest.
  "cron_monthly_recon",
]);

/** Subset that callers can use to recognise cron envelopes. */
export const CRON_ENVELOPE_KINDS = Object.freeze([
  "cron_task_heartbeat",
  "cron_morning_briefing",
  "cron_client_chase",
  "cron_idempotency_cleanup",
  "cron_monthly_recon",
]);

/**
 * @typedef {object} InnerEnvelope
 * @property {number} v
 * @property {string} kind
 * @property {string} issued_at
 * @property {string} nonce
 * @property {number | null} chat_id
 * @property {number | null} [message_id]
 * @property {number | null} [update_id]
 * @property {Record<string, unknown>} payload
 */

/**
 * @typedef {object} OuterEnvelope
 * @property {number} v
 * @property {string} kind
 * @property {string} payload_b64
 * @property {string} signature
 */

/**
 * Build the inner envelope (the thing that gets base64-encoded and signed).
 *
 * @param {object} args
 * @param {string} args.kind
 * @param {number | null} args.chatId
 * @param {number | null} [args.messageId]
 * @param {number | null} [args.updateId]
 * @param {Record<string, unknown>} args.payload
 * @param {number} [args.now]   epoch millis (test seam)
 * @param {string} [args.nonce] (test seam)
 * @returns {InnerEnvelope}
 */
export function buildInnerEnvelope({
  kind,
  chatId,
  messageId = null,
  updateId = null,
  payload,
  now = Date.now(),
  nonce = randomUUID(),
}) {
  if (!ENVELOPE_KINDS.includes(kind)) {
    throw new Error(`unknown envelope kind: ${kind}`);
  }
  if (chatId !== null && !Number.isFinite(chatId)) {
    throw new Error(`chatId must be a finite number or null, got ${String(chatId)}`);
  }
  if (payload === null || typeof payload !== "object") {
    throw new Error("payload must be an object");
  }
  return {
    v: ENVELOPE_VERSION,
    kind,
    issued_at: new Date(now).toISOString(),
    nonce,
    chat_id: chatId,
    message_id: messageId,
    update_id: updateId,
    payload,
  };
}

/**
 * Encode the inner envelope as base64 JSON.
 * @param {InnerEnvelope} inner
 * @returns {string}
 */
export function encodeInner(inner) {
  return Buffer.from(JSON.stringify(inner), "utf8").toString("base64");
}

/**
 * Compute the hex HMAC-SHA256 of `payloadB64` with the shared secret.
 * @param {string} payloadB64
 * @param {string} secret
 * @returns {string}
 */
export function signPayload(payloadB64, secret) {
  if (typeof secret !== "string" || secret.length === 0) {
    throw new Error("HMAC secret must be a non-empty string");
  }
  return createHmac("sha256", secret).update(payloadB64).digest("hex");
}

/**
 * Build the full outer envelope from handler-friendly inputs.
 *
 * @param {object} args
 * @param {string} args.kind
 * @param {number | null} args.chatId
 * @param {number | null} [args.messageId]
 * @param {number | null} [args.updateId]
 * @param {Record<string, unknown>} args.payload
 * @param {string} args.secret
 * @param {number} [args.now]
 * @param {string} [args.nonce]
 * @returns {{ inner: InnerEnvelope, outer: OuterEnvelope }}
 */
export function buildEnvelope({ kind, chatId, messageId, updateId, payload, secret, now, nonce }) {
  const inner = buildInnerEnvelope({ kind, chatId, messageId, updateId, payload, now, nonce });
  const payload_b64 = encodeInner(inner);
  const signature = signPayload(payload_b64, secret);
  /** @type {OuterEnvelope} */
  const outer = {
    v: ENVELOPE_VERSION,
    kind,
    payload_b64,
    signature,
  };
  return { inner, outer };
}
