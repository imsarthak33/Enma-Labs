import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock config before importing handlers.
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

// Mock dispatch so nothing actually fires.
const mockDispatch = vi.fn();
vi.mock("../src/dispatch/backend_client.js", () => ({
  dispatchEnvelopeAsync: (...args) => mockDispatch(...args),
}));

import { handlePhoto } from "../src/routing/photo_handler.js";
import { handleDocument } from "../src/routing/document_handler.js";
import { handleText } from "../src/routing/text_handler.js";
import { handleVoice } from "../src/routing/voice_handler.js";

beforeEach(() => {
  mockDispatch.mockClear();
});

// ---------------------------------------------------------------------------
// photo_handler
// ---------------------------------------------------------------------------
describe("handlePhoto", () => {
  it("dispatches a document envelope with the largest photo", () => {
    const msg = {
      chat: { id: 100 },
      message_id: 1,
      photo: [
        { file_id: "small", file_unique_id: "us", width: 90, height: 90 },
        { file_id: "medium", file_unique_id: "um", width: 320, height: 320 },
        { file_id: "large", file_unique_id: "ul", width: 800, height: 800 },
      ],
      caption: "Invoice ABC",
    };
    handlePhoto(msg, 42);
    expect(mockDispatch).toHaveBeenCalledTimes(1);
    const outer = mockDispatch.mock.calls[0][0];
    expect(outer.kind).toBe("document");
    // Decode payload to verify file_id selection.
    const inner = JSON.parse(Buffer.from(outer.payload_b64, "base64").toString("utf8"));
    expect(inner.payload.file_id).toBe("large");
    expect(inner.payload.caption).toBe("Invoice ABC");
    expect(inner.chat_id).toBe(100);
  });

  it("does nothing when photo array is empty", () => {
    handlePhoto({ chat: { id: 1 }, message_id: 1, photo: [] }, 1);
    expect(mockDispatch).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// document_handler
// ---------------------------------------------------------------------------
describe("handleDocument", () => {
  it("dispatches a document envelope with file metadata", () => {
    const msg = {
      chat: { id: 200 },
      message_id: 2,
      document: {
        file_id: "doc123",
        file_unique_id: "ud",
        file_name: "invoice.pdf",
        mime_type: "application/pdf",
        file_size: 54321,
      },
      caption: null,
    };
    handleDocument(msg, 55);
    expect(mockDispatch).toHaveBeenCalledTimes(1);
    const outer = mockDispatch.mock.calls[0][0];
    expect(outer.kind).toBe("document");
    const inner = JSON.parse(Buffer.from(outer.payload_b64, "base64").toString("utf8"));
    expect(inner.payload.file_name).toBe("invoice.pdf");
    expect(inner.payload.mime_type).toBe("application/pdf");
  });

  it("does nothing when document field is missing", () => {
    handleDocument({ chat: { id: 1 }, message_id: 1 }, 1);
    expect(mockDispatch).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// text_handler
// ---------------------------------------------------------------------------
describe("handleText", () => {
  it("dispatches a command envelope with the text content", () => {
    const msg = {
      chat: { id: 300 },
      message_id: 3,
      text: "/add_client Acme Corp 22AAAAA0000A1Z5",
      entities: [{ type: "bot_command", offset: 0, length: 11 }],
    };
    handleText(msg, 70);
    expect(mockDispatch).toHaveBeenCalledTimes(1);
    const outer = mockDispatch.mock.calls[0][0];
    expect(outer.kind).toBe("command");
    const inner = JSON.parse(Buffer.from(outer.payload_b64, "base64").toString("utf8"));
    expect(inner.payload.text).toBe("/add_client Acme Corp 22AAAAA0000A1Z5");
    expect(inner.payload.entities).toHaveLength(1);
  });

  it("does nothing for empty text", () => {
    handleText({ chat: { id: 1 }, message_id: 1, text: "" }, 1);
    expect(mockDispatch).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// voice_handler
// ---------------------------------------------------------------------------
describe("handleVoice", () => {
  it("dispatches a voice envelope with file and duration", () => {
    const msg = {
      chat: { id: 400 },
      message_id: 4,
      voice: {
        file_id: "voice123",
        file_unique_id: "uv",
        duration: 12,
        mime_type: "audio/ogg",
        file_size: 9999,
      },
    };
    handleVoice(msg, 80);
    expect(mockDispatch).toHaveBeenCalledTimes(1);
    const outer = mockDispatch.mock.calls[0][0];
    expect(outer.kind).toBe("voice");
    const inner = JSON.parse(Buffer.from(outer.payload_b64, "base64").toString("utf8"));
    expect(inner.payload.file_id).toBe("voice123");
    expect(inner.payload.duration).toBe(12);
  });

  it("does nothing when voice field is missing", () => {
    handleVoice({ chat: { id: 1 }, message_id: 1 }, 1);
    expect(mockDispatch).not.toHaveBeenCalled();
  });
});
