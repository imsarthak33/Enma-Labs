import { describe, it, expect } from "vitest";
import { createHmac } from "node:crypto";

import {
  ENVELOPE_VERSION,
  buildInnerEnvelope,
  encodeInner,
  signPayload,
  buildEnvelope,
} from "../src/utils/envelope.js";

const SECRET = "test-hmac-secret";

describe("buildInnerEnvelope", () => {
  it("produces a fully-formed envelope with stable shape", () => {
    const inner = buildInnerEnvelope({
      kind: "document",
      chatId: 999,
      messageId: 42,
      updateId: 7,
      payload: { foo: "bar" },
      now: 0,
      nonce: "fixed-nonce",
    });
    expect(inner).toEqual({
      v: ENVELOPE_VERSION,
      kind: "document",
      issued_at: "1970-01-01T00:00:00.000Z",
      nonce: "fixed-nonce",
      chat_id: 999,
      message_id: 42,
      update_id: 7,
      payload: { foo: "bar" },
    });
  });

  it("rejects an unknown kind", () => {
    expect(() =>
      buildInnerEnvelope({ kind: "nope", chatId: 1, payload: {} }),
    ).toThrow(/kind/);
  });

  it("rejects a non-finite chatId", () => {
    expect(() =>
      buildInnerEnvelope({ kind: "command", chatId: Number.NaN, payload: {} }),
    ).toThrow(/chatId/);
  });

  it("rejects a non-object payload", () => {
    expect(() =>
      buildInnerEnvelope({ kind: "command", chatId: 1, payload: null }),
    ).toThrow(/payload/);
  });
});

describe("encodeInner + signPayload", () => {
  it("base64-encodes then signs the same bytes", () => {
    const inner = buildInnerEnvelope({
      kind: "command",
      chatId: 1,
      payload: { text: "hi" },
      now: 0,
      nonce: "n",
    });
    const b64 = encodeInner(inner);
    expect(b64).toMatch(/^[A-Za-z0-9+/=]+$/);
    const sig = signPayload(b64, SECRET);
    const expected = createHmac("sha256", SECRET).update(b64).digest("hex");
    expect(sig).toBe(expected);
  });

  it("rejects an empty secret", () => {
    expect(() => signPayload("x", "")).toThrow(/secret/);
  });
});

describe("buildEnvelope", () => {
  it("returns a verifiable outer envelope", () => {
    const { inner, outer } = buildEnvelope({
      kind: "voice",
      chatId: 5,
      messageId: 12,
      payload: { file_id: "abc" },
      secret: SECRET,
      now: 1_700_000_000_000,
      nonce: "n",
    });
    expect(outer.v).toBe(ENVELOPE_VERSION);
    expect(outer.kind).toBe("voice");
    // Decoding payload_b64 should round-trip the inner envelope.
    const decoded = JSON.parse(Buffer.from(outer.payload_b64, "base64").toString("utf8"));
    expect(decoded).toEqual(inner);
    // Signature should validate.
    const expectedSig = createHmac("sha256", SECRET)
      .update(outer.payload_b64)
      .digest("hex");
    expect(outer.signature).toBe(expectedSig);
  });

  it("a tampered payload no longer matches the signature", () => {
    const { outer } = buildEnvelope({
      kind: "command",
      chatId: 1,
      payload: { text: "hi" },
      secret: SECRET,
      nonce: "n",
      now: 0,
    });
    const tamperedB64 = Buffer.from(
      JSON.stringify({ tampered: true }),
      "utf8",
    ).toString("base64");
    const recomputed = createHmac("sha256", SECRET).update(tamperedB64).digest("hex");
    expect(recomputed).not.toBe(outer.signature);
  });
});
