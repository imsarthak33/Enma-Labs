import { describe, it, expect, vi, beforeEach } from "vitest";

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

// Mock dispatch.
const mockDispatch = vi.fn();
vi.mock("../src/dispatch/backend_client.js", () => ({
  dispatchEnvelopeAsync: (...args) => mockDispatch(...args),
}));

// Mock individual handlers so we can verify which one gets called.
const mockPhotoHandler = vi.fn();
const mockDocHandler = vi.fn();
const mockTextHandler = vi.fn();
const mockVoiceHandler = vi.fn();

vi.mock("../src/routing/photo_handler.js", () => ({
  handlePhoto: (...args) => mockPhotoHandler(...args),
}));
vi.mock("../src/routing/document_handler.js", () => ({
  handleDocument: (...args) => mockDocHandler(...args),
}));
vi.mock("../src/routing/text_handler.js", () => ({
  handleText: (...args) => mockTextHandler(...args),
}));
vi.mock("../src/routing/voice_handler.js", () => ({
  handleVoice: (...args) => mockVoiceHandler(...args),
}));

import { initRouter, routeUpdate, getBuffer } from "../src/routing/message_router.js";

beforeEach(() => {
  mockDispatch.mockClear();
  mockPhotoHandler.mockClear();
  mockDocHandler.mockClear();
  mockTextHandler.mockClear();
  mockVoiceHandler.mockClear();
  // Re-init the router with a no-op dispatch for the buffer.
  initRouter({ dispatch: vi.fn() });
});

describe("routeUpdate — handler dispatch", () => {
  it("routes a photo message to handlePhoto", () => {
    routeUpdate({
      update_id: 1,
      message: {
        chat: { id: 100 },
        message_id: 10,
        photo: [{ file_id: "p1", file_unique_id: "u1", width: 800, height: 800 }],
      },
    });
    expect(mockPhotoHandler).toHaveBeenCalledTimes(1);
    expect(mockDocHandler).not.toHaveBeenCalled();
    expect(mockTextHandler).not.toHaveBeenCalled();
    expect(mockVoiceHandler).not.toHaveBeenCalled();
  });

  it("routes a document message to handleDocument", () => {
    routeUpdate({
      update_id: 2,
      message: {
        chat: { id: 200 },
        message_id: 20,
        document: { file_id: "d1", file_unique_id: "ud1", file_name: "a.pdf" },
      },
    });
    expect(mockDocHandler).toHaveBeenCalledTimes(1);
  });

  it("routes a text message to handleText", () => {
    routeUpdate({
      update_id: 3,
      message: { chat: { id: 300 }, message_id: 30, text: "hello" },
    });
    expect(mockTextHandler).toHaveBeenCalledTimes(1);
  });

  it("routes a voice message to handleVoice", () => {
    routeUpdate({
      update_id: 4,
      message: {
        chat: { id: 400 },
        message_id: 40,
        voice: { file_id: "v1", file_unique_id: "uv1", duration: 5 },
      },
    });
    expect(mockVoiceHandler).toHaveBeenCalledTimes(1);
  });

  it("routes a callback_query to dispatch as 'callback' envelope", () => {
    routeUpdate({
      update_id: 5,
      callback_query: {
        id: "cbq_1",
        data: "approve",
        from: { id: 500, first_name: "Test" },
        message: { chat: { id: 500 }, message_id: 50 },
      },
    });
    expect(mockDispatch).toHaveBeenCalledTimes(1);
    const outer = mockDispatch.mock.calls[0][0];
    expect(outer.kind).toBe("callback");
  });

  it("routes a photo with media_group_id to the buffer, NOT handlePhoto", () => {
    routeUpdate({
      update_id: 6,
      message: {
        chat: { id: 600 },
        message_id: 60,
        media_group_id: "mg_test",
        photo: [{ file_id: "mg_p1", file_unique_id: "u_mg_p1", width: 800, height: 800 }],
      },
    });
    expect(mockPhotoHandler).not.toHaveBeenCalled();
    expect(getBuffer().pendingGroups).toBe(1);
  });

  it("silently ignores updates with no message and no callback_query", () => {
    // Should not throw.
    routeUpdate({ update_id: 99 });
    expect(mockPhotoHandler).not.toHaveBeenCalled();
    expect(mockDocHandler).not.toHaveBeenCalled();
    expect(mockTextHandler).not.toHaveBeenCalled();
    expect(mockVoiceHandler).not.toHaveBeenCalled();
    expect(mockDispatch).not.toHaveBeenCalled();
  });

  it("silently ignores unrecognised message types (e.g. sticker)", () => {
    routeUpdate({
      update_id: 100,
      message: {
        chat: { id: 700 },
        message_id: 70,
        sticker: { file_id: "stk", file_unique_id: "us" },
      },
    });
    expect(mockPhotoHandler).not.toHaveBeenCalled();
    expect(mockDocHandler).not.toHaveBeenCalled();
    expect(mockTextHandler).not.toHaveBeenCalled();
    expect(mockVoiceHandler).not.toHaveBeenCalled();
  });
});
