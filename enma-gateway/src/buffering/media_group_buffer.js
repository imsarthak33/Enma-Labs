// =============================================================================
// Media group buffer — collects photos/documents that share a media_group_id
// and flushes them as a single "document_batch" envelope after a 3000ms
// collection window.
//
// Telegram delivers each photo in a media group as a separate update, each
// sharing the same media_group_id. Without buffering, we'd dispatch 5 photos
// as 5 individual documents. With buffering, we collect them all and flush
// once after 3s of silence from that group.
//
// Lifecycle:
//   1. First update for a new media_group_id → start timer.
//   2. Subsequent updates with the same id → append to buffer, no timer reset.
//   3. Timer fires after 3000ms → flush all collected items as one batch.
//   4. Buffer for that group is deleted.
// =============================================================================

import { buildEnvelope } from "../utils/envelope.js";
import { config } from "../config.js";
import { logger } from "../utils/logger.js";
import { dispatchEnvelopeAsync } from "../dispatch/backend_client.js";

const DEFAULT_FLUSH_MS = 3000;

/**
 * @typedef {object} BufferedItem
 * @property {string}      type           "photo" | "file"
 * @property {string}      file_id
 * @property {string}      file_unique_id
 * @property {number|null} [width]
 * @property {number|null} [height]
 * @property {string|null} [file_name]
 * @property {string|null} [mime_type]
 * @property {number|null} [file_size]
 * @property {string|null} [caption]
 * @property {number}      message_id
 */

/**
 * @typedef {object} BufferEntry
 * @property {BufferedItem[]} items
 * @property {number}         chatId
 * @property {ReturnType<typeof setTimeout>} timer
 */

export class MediaGroupBuffer {
  /**
   * @param {object} [opts]
   * @param {number} [opts.flushMs]   Collection window in ms (default 3000)
   * @param {(outer: object) => void} [opts.dispatch]  Override for testing
   */
  constructor(opts = {}) {
    this.flushMs = opts.flushMs ?? DEFAULT_FLUSH_MS;
    this._dispatch = opts.dispatch ?? dispatchEnvelopeAsync;
    /** @type {Map<string, BufferEntry>} */
    this._groups = new Map();
  }

  /**
   * Add a photo to the buffer.
   * @param {string} mediaGroupId
   * @param {object} message   Telegram Message
   * @param {number} updateId
   */
  addPhoto(mediaGroupId, message, updateId) {
    const photos = message.photo || [];
    const best = photos[photos.length - 1];
    if (!best) return;

    this._add(mediaGroupId, message, updateId, {
      type: "photo",
      file_id: best.file_id,
      file_unique_id: best.file_unique_id,
      width: best.width,
      height: best.height,
      caption: message.caption || null,
      message_id: message.message_id,
    });
  }

  /**
   * Add a document/file to the buffer.
   * @param {string} mediaGroupId
   * @param {object} message   Telegram Message
   * @param {number} updateId
   */
  addDocument(mediaGroupId, message, updateId) {
    const doc = message.document;
    if (!doc) return;

    this._add(mediaGroupId, message, updateId, {
      type: "file",
      file_id: doc.file_id,
      file_unique_id: doc.file_unique_id,
      file_name: doc.file_name || null,
      mime_type: doc.mime_type || null,
      file_size: doc.file_size || null,
      caption: message.caption || null,
      message_id: message.message_id,
    });
  }

  /**
   * @param {string} mediaGroupId
   * @param {object} message
   * @param {number} updateId
   * @param {BufferedItem} item
   */
  _add(mediaGroupId, message, updateId, item) {
    const existing = this._groups.get(mediaGroupId);
    if (existing) {
      existing.items.push(item);
      logger.debug("media_group_item_buffered", {
        media_group_id: mediaGroupId,
        count: existing.items.length,
      });
      return;
    }

    // First item for this group — start the flush timer.
    const entry = {
      items: [item],
      chatId: message.chat.id,
      timer: setTimeout(() => this._flush(mediaGroupId), this.flushMs),
    };
    this._groups.set(mediaGroupId, entry);
    logger.debug("media_group_started", {
      media_group_id: mediaGroupId,
      chat_id: message.chat.id,
    });
  }

  /**
   * Flush a completed media group as a single document_batch envelope.
   * @param {string} mediaGroupId
   */
  _flush(mediaGroupId) {
    const entry = this._groups.get(mediaGroupId);
    if (!entry) return;
    this._groups.delete(mediaGroupId);

    const { outer } = buildEnvelope({
      kind: "document_batch",
      chatId: entry.chatId,
      messageId: null, // batch has no single message_id
      payload: {
        media_group_id: mediaGroupId,
        items: entry.items,
        count: entry.items.length,
      },
      secret: config.backend.hmacSecret,
    });

    logger.info("media_group_flushed", {
      media_group_id: mediaGroupId,
      chat_id: entry.chatId,
      count: entry.items.length,
    });

    this._dispatch(outer);
  }

  /** Number of groups currently buffering. */
  get pendingGroups() {
    return this._groups.size;
  }

  /** Cancel all pending timers (for graceful shutdown). */
  clear() {
    for (const [, entry] of this._groups) {
      clearTimeout(entry.timer);
    }
    this._groups.clear();
  }
}
