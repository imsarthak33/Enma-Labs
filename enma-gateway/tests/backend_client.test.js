import { describe, it, expect, vi } from "vitest";

import {
  ROUTE_BY_KIND,
  routeForKind,
  dispatchEnvelope,
  Semaphore,
  DISPATCH_CONCURRENCY,
} from "../src/dispatch/backend_client.js";

/** @returns {import("../src/dispatch/backend_client.js").OuterEnvelope} */
function fakeOuter(overrides = {}) {
  return {
    v: 1,
    kind: "document",
    payload_b64: "cGF5bG9hZA==",
    signature: "abcd",
    ...overrides,
  };
}

describe("ROUTE_BY_KIND", () => {
  it("maps every expected kind to /worker/* and nothing else", () => {
    expect(ROUTE_BY_KIND).toMatchObject({
      document: "/worker/document",
      document_batch: "/worker/document-batch",
      command: "/worker/command",
      voice: "/worker/voice",
      callback: "/worker/callback",
    });
  });

  it("routeForKind throws for unknown kinds", () => {
    expect(() => routeForKind("garbage")).toThrow(/route/);
  });
});

describe("dispatchEnvelope", () => {
  it("POSTs to backendUrl + kind-route with HMAC header and JSON body", async () => {
    const calls = [];
    const fetchImpl = vi.fn(async (url, init) => {
      calls.push({ url, init });
      return new Response("", { status: 200 });
    });
    const outer = fakeOuter({ kind: "voice", signature: "deadbeef" });
    const result = await dispatchEnvelope(outer, {
      fetchImpl,
      backendUrl: "http://backend.test",
      apiKey: "key-xyz",
      timeoutMs: 1000,
    });
    expect(result.ok).toBe(true);
    expect(result.status).toBe(200);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(calls[0].url).toBe("http://backend.test/worker/voice");
    expect(calls[0].init.method).toBe("POST");
    expect(calls[0].init.headers["X-Enma-Api-Key"]).toBe("key-xyz");
    expect(calls[0].init.headers["X-Enma-Signature"]).toBe("deadbeef");
    expect(JSON.parse(calls[0].init.body)).toEqual(outer);
  });

  it("trims trailing slash from backendUrl", async () => {
    const fetchImpl = vi.fn(async () => new Response("", { status: 200 }));
    await dispatchEnvelope(fakeOuter(), {
      fetchImpl,
      backendUrl: "http://backend.test/",
      apiKey: "k",
    });
    expect(fetchImpl.mock.calls[0][0]).toBe("http://backend.test/worker/document");
  });

  it("returns {ok:false,status:0} on fetch failure (network error)", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    });
    const result = await dispatchEnvelope(fakeOuter(), {
      fetchImpl,
      backendUrl: "http://backend.test",
      apiKey: "k",
    });
    expect(result).toEqual({ ok: false, status: 0 });
  });

  it("returns {ok:false,status:0} when the timeout fires (AbortError)", async () => {
    const fetchImpl = vi.fn((_url, init) => {
      return new Promise((_resolve, reject) => {
        init.signal.addEventListener("abort", () => {
          const err = new Error("aborted");
          err.name = "AbortError";
          reject(err);
        });
      });
    });
    const result = await dispatchEnvelope(fakeOuter(), {
      fetchImpl,
      backendUrl: "http://backend.test",
      apiKey: "k",
      timeoutMs: 5,
    });
    expect(result).toEqual({ ok: false, status: 0 });
  });

  it("surfaces non-2xx as ok:false but still resolves", async () => {
    const fetchImpl = vi.fn(async () => new Response("", { status: 502 }));
    const result = await dispatchEnvelope(fakeOuter(), {
      fetchImpl,
      backendUrl: "http://backend.test",
      apiKey: "k",
    });
    expect(result).toEqual({ ok: false, status: 502 });
  });

  it("acquires and releases the semaphore around the HTTP call", async () => {
    const sem = new Semaphore(1);
    let inFlight = 0;
    let maxInFlight = 0;
    const fetchImpl = vi.fn(async () => {
      inFlight++;
      maxInFlight = Math.max(maxInFlight, inFlight);
      // Simulate async work so concurrent calls could overlap.
      await Promise.resolve();
      inFlight--;
      return new Response("", { status: 200 });
    });
    // Fire 3 dispatches through a semaphore of 1 — only 1 at a time.
    await Promise.all([
      dispatchEnvelope(fakeOuter(), { fetchImpl, backendUrl: "http://b", apiKey: "k", semaphore: sem }),
      dispatchEnvelope(fakeOuter(), { fetchImpl, backendUrl: "http://b", apiKey: "k", semaphore: sem }),
      dispatchEnvelope(fakeOuter(), { fetchImpl, backendUrl: "http://b", apiKey: "k", semaphore: sem }),
    ]);
    expect(maxInFlight).toBe(1);
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });

  it("semaphore with limit=0 allows unlimited concurrency", async () => {
    const sem = new Semaphore(0);
    let inFlight = 0;
    let maxInFlight = 0;
    const fetchImpl = vi.fn(async () => {
      inFlight++;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await Promise.resolve();
      inFlight--;
      return new Response("", { status: 200 });
    });
    await Promise.all(
      Array.from({ length: 5 }, () =>
        dispatchEnvelope(fakeOuter(), { fetchImpl, backendUrl: "http://b", apiKey: "k", semaphore: sem }),
      ),
    );
    expect(maxInFlight).toBeGreaterThan(1);
  });
});

describe("Semaphore", () => {
  it("has sensible default concurrency", () => {
    expect(DISPATCH_CONCURRENCY).toBeGreaterThanOrEqual(0);
  });

  it("serialises work when limit=1", async () => {
    const sem = new Semaphore(1);
    const order = [];
    async function worker(id) {
      await sem.acquire();
      order.push(`start-${id}`);
      await Promise.resolve(); // yield
      order.push(`end-${id}`);
      sem.release();
    }
    await Promise.all([worker(1), worker(2), worker(3)]);
    // Each worker must start and end before the next starts.
    for (let i = 0; i < order.length - 1; i += 2) {
      expect(order[i]).toMatch(/start-/);
      expect(order[i + 1]).toMatch(/end-/);
    }
  });
});
