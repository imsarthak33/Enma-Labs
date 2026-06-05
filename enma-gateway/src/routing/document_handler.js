// =============================================================================
// Document handler — PDF / file uploads → "document" envelope.
//
// Telegram sends file metadata (file_id, file_name, mime_type) but not the
// actual bytes. The backend downloads the file via Telegram's getFile API
// using the file_id we forward.
// =============================================================================

import { buildEnvelope } from "../utils/envelope.js";
import { config } from "../config.js";
import { logger } from "../utils/logger.js";
import { dispatchEnvelopeAsync } from "../dispatch/backend_client.js";

/**
 * @param {object} message  Telegram Message object
 * @param {number} updateId Telegram update_id
 */
export function handleDocument(message, updateId) {
  const doc = message.document;
  if (!doc) {
    logger.warn("document_handler_no_document", { update_id: updateId });
    return;
  }

  const { outer } = buildEnvelope({
    kind: "document",
    chatId: message.chat.id,
    messageId: message.message_id,
    updateId,
    payload: {
      type: "file",
      file_id: doc.file_id,
      file_unique_id: doc.file_unique_id,
      file_name: doc.file_name || null,
      mime_type: doc.mime_type || null,
      file_size: doc.file_size || null,
      caption: message.caption || null,
    },
    secret: config.backend.hmacSecret,
  });

  logger.info("document_dispatched", {
    chat_id: message.chat.id,
    message_id: message.message_id,
    file_name: doc.file_name,
    mime_type: doc.mime_type,
  });

  dispatchEnvelopeAsync(outer);
}
