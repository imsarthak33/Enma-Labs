// =============================================================================
// Photo handler — single photo (no media group) → "document" envelope.
//
// Telegram sends an array of PhotoSize objects sorted smallest-to-largest.
// We always take the last element (highest resolution) for extraction quality.
// =============================================================================

import { buildEnvelope } from "../utils/envelope.js";
import { config } from "../config.js";
import { logger } from "../utils/logger.js";
import { dispatchEnvelopeAsync } from "../dispatch/backend_client.js";

/**
 * @param {object} message  Telegram Message object
 * @param {number} updateId Telegram update_id
 */
export function handlePhoto(message, updateId) {
  const photos = message.photo;
  if (!photos || photos.length === 0) {
    logger.warn("photo_handler_no_photos", { update_id: updateId });
    return;
  }

  const best = photos[photos.length - 1]; // highest resolution

  const { outer } = buildEnvelope({
    kind: "document",
    chatId: message.chat.id,
    messageId: message.message_id,
    updateId,
    payload: {
      type: "photo",
      file_id: best.file_id,
      file_unique_id: best.file_unique_id,
      width: best.width,
      height: best.height,
      caption: message.caption || null,
    },
    secret: config.backend.hmacSecret,
  });

  logger.info("photo_dispatched", {
    chat_id: message.chat.id,
    message_id: message.message_id,
    file_id: best.file_id,
  });

  dispatchEnvelopeAsync(outer);
}
