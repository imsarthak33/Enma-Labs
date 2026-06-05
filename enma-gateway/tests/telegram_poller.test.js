import { describe, it, expect, vi } from "vitest";

// Mock config.
vi.mock("../src/config.js", () => ({
  config: {
    backend: {
      hmacSecret: "test-secret",
      url: "http://localhost:8000",
      apiKey: "test-key",
    },
    telegram: { botToken: "test-token" },
    logLevel: "error",
    service: { name: "enma-gateway", version: "0.1.0" },
    env: "test",
    isProduction: false,
  },
}));

import { startPoller } from "../src/polling/telegram_poller.js";

/** Small real-timer delay to let the async poll loop advance. */
const tick = (ms = 80) => new Promise((r) => setTimeout(r, ms));

/**
 * Build a mock fetch that serves pre-canned responses in order, then hangs
 * on subsequent calls until the AbortController signal fires (so stop()
 * can cleanly break the loop).
 *
 * @param {Array<{ ok: boolean, status?: number, result?: object[] }>} responses
 */
function buildFetch(responses) {
  let callIndex = 0;
  const fetchMock = vi.fn(async (_url, opts) => {
    const idx = callIndex++;
    if (idx < responses.length) {
      const r = responses[idx];
      if (!r.ok) {
        return { ok: false, status: r.status ?? 500 };
      }
      return {
        ok: true,
        json: async () => ({ ok: true, result: r.result ?? [] }),
      };
    }
    // All canned responses consumed — hang until abort signal fires.
    return new Promise((_resolve, reject) => {
      const onAbort = () => {
        const err = new Error("The operation was aborted");
        err.name = "AbortError";
        reject(err);
      };
      if (opts?.signal?.aborted) {
        onAbort();
        return;
      }
      opts?.signal?.addEventListener("abort", onAbort);
    });
  });
  return fetchMock;
}

describe("TelegramPoller", () => {
  it("calls routeFn for each update and advances offset", async () => {
    const routed = [];
    const fetchMock = buildFetch([
      {
        ok: true,
        result: [
          { update_id: 10, message: { chat: { id: 1 }, text: "a" } },
          { update_id: 11, message: { chat: { id: 1 }, text: "b" } },
        ],
      },
    ]);

    const { stop } = startPoller({
      routeFn: (u) => routed.push(u),
      fetchImpl: fetchMock,
      botToken: "test-token",
      pollTimeoutS: 1,
    });

    // Let the first poll cycle complete.
    await tick();

    expect(routed).toHaveLength(2);
    expect(routed[0].update_id).toBe(10);
    expect(routed[1].update_id).toBe(11);

    // Verify offset was advanced: second fetch should have offset=12.
    const secondCallUrl = fetchMock.mock.calls[1]?.[0];
    if (secondCallUrl) {
      expect(secondCallUrl).toContain("offset=12");
    }

    await stop();
  });

  it("deduplicates re-delivered updates", async () => {
    const routed = [];
    const fetchMock = buildFetch([
      {
        ok: true,
        result: [{ update_id: 50, message: { chat: { id: 1 }, text: "x" } }],
      },
      {
        ok: true,
        // Re-deliver the same update_id.
        result: [{ update_id: 50, message: { chat: { id: 1 }, text: "x" } }],
      },
    ]);

    const { stop } = startPoller({
      routeFn: (u) => routed.push(u),
      fetchImpl: fetchMock,
      botToken: "test-token",
      pollTimeoutS: 1,
    });

    await tick(150);

    // Should only have routed once despite two deliveries.
    expect(routed).toHaveLength(1);
    expect(routed[0].update_id).toBe(50);

    await stop();
  });

  it(
    "backs off on API errors then recovers",
    async () => {
      const routed = [];
      const fetchMock = buildFetch([
        // First call: 500 error → triggers 500ms backoff.
        { ok: false, status: 500 },
        // Second call (after backoff): success.
        {
          ok: true,
          result: [{ update_id: 1, message: { chat: { id: 1 }, text: "ok" } }],
        },
      ]);

      const { stop } = startPoller({
        routeFn: (u) => routed.push(u),
        fetchImpl: fetchMock,
        botToken: "test-token",
        pollTimeoutS: 1,
      });

      // Wait long enough for: fetch(500) + 500ms backoff + fetch(ok).
      await tick(800);

      expect(routed).toHaveLength(1);
      expect(routed[0].update_id).toBe(1);

      await stop();
    },
    { timeout: 10_000 },
  );

  it("stop() aborts the current fetch and exits the loop", async () => {
    // Fetch that hangs from the very first call (abort-aware).
    const fetchMock = buildFetch([]);

    const { stop } = startPoller({
      routeFn: vi.fn(),
      fetchImpl: fetchMock,
      botToken: "test-token",
      pollTimeoutS: 1,
    });

    await tick(50);
    // stop() should resolve cleanly — not hang.
    await stop();
    expect(true).toBe(true);
  });

  it("throws if routeFn is not provided", () => {
    expect(() => startPoller({ fetchImpl: vi.fn(), botToken: "t" })).toThrow(/routeFn/);
  });
});
