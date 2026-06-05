// =============================================================================
// LRU-style dedup cache for Telegram update_id values.
//
// Telegram getUpdates is at-least-once: a network blip can re-deliver the
// same update before our `offset` ACK lands. We keep a 1000-entry recency
// window so re-deliveries are silently dropped before reaching the router.
//
// Backed by Map insertion order (V8 maintains it deterministically): touching
// an existing key is delete+set so the most-recently-seen id sits at the tail
// and the oldest sits at the head for eviction.
// =============================================================================

const DEFAULT_CAPACITY = 1000;

export class DedupCache {
  /**
   * @param {number} [capacity]
   */
  constructor(capacity = DEFAULT_CAPACITY) {
    if (!Number.isInteger(capacity) || capacity <= 0) {
      throw new Error(`DedupCache capacity must be a positive integer, got ${capacity}`);
    }
    this.capacity = capacity;
    /** @type {Map<string | number, true>} */
    this._map = new Map();
  }

  /**
   * Check membership and refresh recency. Returns true if the key was already
   * present (caller should drop the update). Returns false if the key was
   * new (and now recorded).
   * @param {string | number} key
   * @returns {boolean}
   */
  seen(key) {
    if (this._map.has(key)) {
      // Refresh recency: move to tail.
      this._map.delete(key);
      this._map.set(key, true);
      return true;
    }
    this._map.set(key, true);
    if (this._map.size > this.capacity) {
      // Evict oldest (Map iteration is insertion order).
      const oldest = this._map.keys().next().value;
      this._map.delete(oldest);
    }
    return false;
  }

  get size() {
    return this._map.size;
  }

  clear() {
    this._map.clear();
  }
}
