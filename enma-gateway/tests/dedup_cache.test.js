import { describe, it, expect } from "vitest";

import { DedupCache } from "../src/polling/dedup_cache.js";

describe("DedupCache", () => {
  it("returns false for a new key and true for a repeat", () => {
    const cache = new DedupCache(10);
    expect(cache.seen(1)).toBe(false);
    expect(cache.seen(1)).toBe(true);
  });

  it("evicts the oldest entry when capacity is exceeded", () => {
    const cache = new DedupCache(3);
    cache.seen("a");
    cache.seen("b");
    cache.seen("c");
    cache.seen("d"); // 'a' should now be evicted
    expect(cache.size).toBe(3);
    expect(cache.seen("a")).toBe(false); // 'a' was evicted → treated as new
    // 'a' getting re-added evicts the next-oldest entry ('b').
    expect(cache.seen("b")).toBe(false);
  });

  it("evicts oldest after exactly capacity+1 inserts (1001 → 1000)", () => {
    const cache = new DedupCache(1000);
    for (let i = 0; i < 1001; i += 1) cache.seen(i);
    expect(cache.size).toBe(1000);
    // The very first id (0) is the only one that should now be evicted.
    expect(cache.seen(0)).toBe(false);
  });

  it("seen() refreshes recency — touched key is not evicted", () => {
    const cache = new DedupCache(3);
    cache.seen("a");
    cache.seen("b");
    cache.seen("c");
    cache.seen("a"); // refreshes 'a' to most-recent; 'b' is now oldest
    cache.seen("d"); // evicts 'b', not 'a'
    expect(cache.seen("a")).toBe(true);
    expect(cache.seen("b")).toBe(false);
  });

  it("rejects non-positive or non-integer capacity", () => {
    expect(() => new DedupCache(0)).toThrow(/capacity/);
    expect(() => new DedupCache(-5)).toThrow(/capacity/);
    expect(() => new DedupCache(1.5)).toThrow(/capacity/);
  });

  it("clear() empties the cache", () => {
    const cache = new DedupCache(10);
    cache.seen(1);
    cache.seen(2);
    cache.clear();
    expect(cache.size).toBe(0);
    expect(cache.seen(1)).toBe(false);
  });
});
