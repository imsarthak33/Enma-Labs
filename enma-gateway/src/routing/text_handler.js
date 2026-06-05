// =============================================================================
// Text handler — text messages and bot commands → "command" envelope.
//
// Every text message (including /start, /help, /add_client, free-text queries)
// is dispatched as a "command" envelope. The backend supervisor agent decides
// whether it's a slash-command or a natural-language query.
// =============================================================================

import { buildEnvelope } from "../utils/envelope.js";
import { config } from "../config.js";
import { logger } from "../utils/logger.js";
import { dispatchEnvelopeAsync } from "../dispatch/backend_client.js";

/**
 * @param {object} message  Telegram Message object
 * @param {number} updateId Telegram update_id
 */
export function handleText(message, updateId) {
  const text = message.text;
  if (typeof text !== "string" || text.length === 0) {
    logger.warn("text_handler_empty", { update_id: updateId });
    return;
  }

  const { outer } = buildEnvelope({
    kind: "command",
    chatId: message.chat.id,
    messageId: message.message_id,
    updateId,
    payload: {
      text,
      entities: message.entities || [],
    },
    secret: config.backend.hmacSecret,
  });

  logger.info("text_dispatched", {
    chat_id: message.chat.id,
    message_id: message.message_id,
    text_length: text.length,
  });

  dispatchEnvelopeAsync(outer);
}
