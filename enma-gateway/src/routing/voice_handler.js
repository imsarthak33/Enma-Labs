// =============================================================================
// Voice handler — voice notes → "voice" envelope.
//
// Telegram encodes voice notes as OGG/Opus. The backend's Whisper service
// handles transcription; we just forward file_id and duration metadata.
// =============================================================================

import { buildEnvelope } from "../utils/envelope.js";
import { config } from "../config.js";
import { logger } from "../utils/logger.js";
import { dispatchEnvelopeAsync } from "../dispatch/backend_client.js";

/**
 * @param {object} message  Telegram Message object
 * @param {number} updateId Telegram update_id
 */
export function handleVoice(message, updateId) {
  const voice = message.voice;
  if (!voice) {
    logger.warn("voice_handler_no_voice", { update_id: updateId });
    return;
  }

  const { outer } = buildEnvelope({
    kind: "voice",
    chatId: message.chat.id,
    messageId: message.message_id,
    updateId,
    payload: {
      file_id: voice.file_id,
      file_unique_id: voice.file_unique_id,
      duration: voice.duration,
      mime_type: voice.mime_type || "audio/ogg",
      file_size: voice.file_size || null,
    },
    secret: config.backend.hmacSecret,
  });

  logger.info("voice_dispatched", {
    chat_id: message.chat.id,
    message_id: message.message_id,
    duration: voice.duration,
  });

  dispatchEnvelopeAsync(outer);
}
