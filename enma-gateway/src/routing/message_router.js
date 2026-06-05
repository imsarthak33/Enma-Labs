// =============================================================================
// Message router — classifies each Telegram update and delegates to the
// appropriate handler or media group buffer.
//
// Classification priority:
//   1. callback_query   → "callback" envelope
//   2. message.photo    → if media_group_id → buffer, else → photo_handler
//   3. message.document → if media_group_id → buffer, else → document_handler
//   4. message.voice    → voice_handler
//   5. message.text     → text_handler
//   6. everything else  → logged and dropped
// =============================================================================

import { logger } from "../utils/logger.js";
import { buildEnvelope } from "../utils/envelope.js";
import { config } from "../config.js";
import { dispatchEnvelopeAsync } from "../dispatch/backend_client.js";
import { handlePhoto } from "./photo_handler.js";
import { handleDocument } from "./document_handler.js";
import { handleText } from "./text_handler.js";
import { handleVoice } from "./voice_handler.js";
import { MediaGroupBuffer } from "../buffering/media_group_buffer.js";

/** @type {MediaGroupBuffer} */
let buffer;

/**
 * Initialise the router's media-group buffer.
 * Must be called once at startup (from index.js or tests).
 *
 * @param {object} [opts]
 * @param {number} [opts.flushMs]
 * @param {(outer: object) => void} [opts.dispatch]
 * @returns {MediaGroupBuffer}
 */
export function initRouter(opts = {}) {
  buffer = new MediaGroupBuffer(opts);
  return buffer;
}

/**
 * Route a single Telegram Update object to the appropriate handler.
 *
 * @param {object} update  Raw Telegram Update
 */
export function routeUpdate(update) {
  const updateId = update.update_id;

  // --- Callback queries (inline keyboard presses) --------------------------
  if (update.callback_query) {
    const cbq = update.callback_query;
    const { outer } = buildEnvelope({
      kind: "callback",
      chatId: cbq.message?.chat?.id ?? null,
      messageId: cbq.message?.message_id ?? null,
      updateId,
      payload: {
        callback_query_id: cbq.id,
        data: cbq.data || null,
        from: cbq.from,
      },
      secret: config.backend.hmacSecret,
    });

    logger.info("callback_dispatched", {
      update_id: updateId,
      callback_data: cbq.data,
    });

    dispatchEnvelopeAsync(outer);
    return;
  }

  // --- Regular messages ----------------------------------------------------
  const msg = update.message;
  if (!msg) {
    logger.debug("update_ignored_no_message", { update_id: updateId });
    return;
  }

  const mediaGroupId = msg.media_group_id;

  // Photos (single or grouped)
  if (msg.photo) {
    if (mediaGroupId) {
      buffer.addPhoto(mediaGroupId, msg, updateId);
    } else {
      handlePhoto(msg, updateId);
    }
    return;
  }

  // Documents / files (single or grouped)
  if (msg.document) {
    if (mediaGroupId) {
      buffer.addDocument(mediaGroupId, msg, updateId);
    } else {
      handleDocument(msg, updateId);
    }
    return;
  }

  // Voice notes
  if (msg.voice) {
    handleVoice(msg, updateId);
    return;
  }

  // Text (including bot commands)
  if (msg.text) {
    handleText(msg, updateId);
    return;
  }

  // Everything else — log and drop
  logger.debug("update_unhandled", {
    update_id: updateId,
    chat_id: msg.chat?.id,
    keys: Object.keys(msg).join(","),
  });
}

/**
 * Returns the current media group buffer instance (for shutdown / testing).
 * @returns {MediaGroupBuffer}
 */
export function getBuffer() {
  return buffer;
}
