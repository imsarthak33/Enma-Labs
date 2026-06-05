import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

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

// Silence dispatch — we capture calls via the dispatch option.
vi.mock("../src/dispatch/backend_client.js", () => ({
  dispatchEnvelopeAsync: vi.fn(),
}));

import { MediaGroupBuffer } from "../src/buffering/media_group_buffer.js";

/** Helper: build a fake Telegram photo message. */
function photoMsg(chatId, msgId, fileId, mediaGroupId) {
  return {
    chat: { id: chatId },
    message_id: msgId,
    media_group_id: mediaGroupId,
    photo: [
      { file_id: `${fileId}_s`, file_unique_id: `u_${fileId}_s`, width: 90, height: 90 },
      { file_id: fileId, file_unique_id: `u_${fileId}`, width: 800, height: 800 },
    ],
    caption: null,
  };
}

describe("MediaGroupBuffer", () => {
  /** @type {MediaGroupBuffer} */
  let buffer;
  /** @type {import("vitest").Mock} */
  let dispatchSpy;

  beforeEach(() => {
    vi.useFakeTimers();
    dispatchSpy = vi.fn();
    buffer = new MediaGroupBuffer({ flushMs: 3000, dispatch: dispatchSpy });
  });

  afterEach(() => {
    buffer.clear();
    vi.useRealTimers();
  });

  it("flushes 5 photos with the same media_group_id as a single batch after 3000ms", () => {
    const groupId = "mg_001";
    for (let i = 1; i <= 5; i++) {
      buffer.addPhoto(groupId, photoMsg(100, i, `photo_${i}`, groupId), 100 + i);
    }
    expect(dispatchSpy).not.toHaveBeenCalled();
    expect(buffer.pendingGroups).toBe(1);

    // Advance time past the flush window.
    vi.advanceTimersByTime(3000);

    expect(dispatchSpy).toHaveBeenCalledTimes(1);
    const outer = dispatchSpy.mock.calls[0][0];
    expect(outer.kind).toBe("document_batch");

    const inner = JSON.parse(Buffer.from(outer.payload_b64, "base64").toString("utf8"));
    expect(inner.payload.count).toBe(5);
    expect(inner.payload.items).toHaveLength(5);
    expect(inner.payload.media_group_id).toBe(groupId);
    expect(inner.chat_id).toBe(100);
    expect(buffer.pendingGroups).toBe(0);
  });

  it("does NOT flush before 3000ms have elapsed", () => {
    buffer.addPhoto("mg_002", photoMsg(200, 1, "p1", "mg_002"), 201);
    vi.advanceTimersByTime(2999);
    expect(dispatchSpy).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(dispatchSpy).toHaveBeenCalledTimes(1);
  });

  it("keeps separate groups independent", () => {
    buffer.addPhoto("g1", photoMsg(300, 1, "a1", "g1"), 301);
    buffer.addPhoto("g2", photoMsg(300, 2, "b1", "g2"), 302);
    buffer.addPhoto("g1", photoMsg(300, 3, "a2", "g1"), 303);

    expect(buffer.pendingGroups).toBe(2);
    vi.advanceTimersByTime(3000);

    expect(dispatchSpy).toHaveBeenCalledTimes(2);
    // First flush should be g1 with 2 items.
    const batch1 = JSON.parse(
      Buffer.from(dispatchSpy.mock.calls[0][0].payload_b64, "base64").toString("utf8"),
    );
    const batch2 = JSON.parse(
      Buffer.from(dispatchSpy.mock.calls[1][0].payload_b64, "base64").toString("utf8"),
    );
    const counts = [batch1.payload.count, batch2.payload.count].sort();
    expect(counts).toEqual([1, 2]);
  });

  it("buffers documents (files) in addition to photos", () => {
    const msg = {
      chat: { id: 400 },
      message_id: 10,
      media_group_id: "mg_docs",
      document: {
        file_id: "pdf1",
        file_unique_id: "u_pdf1",
        file_name: "a.pdf",
        mime_type: "application/pdf",
        file_size: 1234,
      },
      caption: "batch pdf",
    };
    buffer.addDocument("mg_docs", msg, 410);
    vi.advanceTimersByTime(3000);

    expect(dispatchSpy).toHaveBeenCalledTimes(1);
    const inner = JSON.parse(
      Buffer.from(dispatchSpy.mock.calls[0][0].payload_b64, "base64").toString("utf8"),
    );
    expect(inner.payload.items[0].type).toBe("file");
    expect(inner.payload.items[0].file_name).toBe("a.pdf");
  });

  it("clear() cancels pending timers and empties the buffer", () => {
    buffer.addPhoto("mg_x", photoMsg(500, 1, "x1", "mg_x"), 501);
    buffer.clear();
    vi.advanceTimersByTime(5000);
    expect(dispatchSpy).not.toHaveBeenCalled();
    expect(buffer.pendingGroups).toBe(0);
  });
});
