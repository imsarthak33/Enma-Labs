// =============================================================================
// Sentry scrubbing tests.
// We don't exercise initSentry() against a real DSN — we test the pure
// scrubber that runs on every event before transmission.
// =============================================================================

import { describe, it, expect } from "vitest";

import { maskObject, scrubEvent } from "../src/observability/sentry.js";

describe("maskObject", () => {
  it("masks top-level secret keys but keeps a short prefix", () => {
    const out = maskObject({ telegram_bot_token: "1234567890:ABCDEF", ok: "yes" });
    expect(out.telegram_bot_token).toMatch(/^1234/);
    expect(out.telegram_bot_token).not.toContain("ABCDEF");
    expect(out.ok).toBe("yes");
  });

  it("recurses into nested objects", () => {
    const out = maskObject({ upstream: { auth: { hmac_secret: "topsecretvalue" } } });
    expect(JSON.stringify(out)).not.toContain("topsecretvalue");
  });

  it("walks arrays of objects", () => {
    const out = maskObject({ clients: [{ gstin: "07AAAAA0000A1Z5" }] });
    expect(out.clients[0].gstin).not.toContain("0000");
  });

  it("leaves plain arrays untouched", () => {
    expect(maskObject({ tags: ["a", "b"] })).toEqual({ tags: ["a", "b"] });
  });

  it("substring match catches compound key names", () => {
    const out = maskObject({ user_pan_history: "ABCDE1234F" });
    expect(out.user_pan_history).not.toContain("ABCDE1234F");
  });
});

describe("scrubEvent", () => {
  it("redacts request body wholesale", () => {
    const event = { request: { data: { chat_id: 42, file_id: "ABC" } } };
    const out = scrubEvent(event);
    expect(out.request.data).toBe("[REDACTED]");
  });

  it("masks request headers", () => {
    const event = { request: { headers: { authorization: "Bearer secrettoken" } } };
    const out = scrubEvent(event);
    expect(JSON.stringify(out.request.headers)).not.toContain("secrettoken");
  });

  it("scrubs breadcrumb data while preserving message", () => {
    const event = {
      breadcrumbs: [
        { category: "http", data: { api_key: "supersecretkey" } },
        { category: "log", message: "hello" },
      ],
    };
    const out = scrubEvent(event);
    expect(JSON.stringify(out.breadcrumbs[0].data)).not.toContain("supersecretkey");
    expect(out.breadcrumbs[1].message).toBe("hello");
  });

  it("trims user object to id only", () => {
    const event = { user: { id: "u1", email: "a@b.co", ip_address: "1.2.3.4" } };
    const out = scrubEvent(event);
    expect(out.user).toEqual({ id: "u1" });
  });

  it("clears user object entirely when id missing", () => {
    const event = { user: { email: "a@b.co" } };
    const out = scrubEvent(event);
    expect(out.user).toEqual({});
  });

  it("passes clean events through unchanged", () => {
    const event = { message: "hi" };
    expect(scrubEvent(event)).toEqual({ message: "hi" });
  });
});
