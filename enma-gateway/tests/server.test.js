import { describe, it, expect, beforeAll, afterAll } from "vitest";

let serverHandle;
let baseUrl;

beforeAll(async () => {
  process.env.NODE_ENV = "test";
  process.env.PORT = "0"; // 0 → pick a free port (we read it back)
  const { startServer } = await import("../src/server.js");
  serverHandle = startServer(0);
  await new Promise((r) => setTimeout(r, 50));
  const addr = serverHandle.server.address();
  if (typeof addr === "string" || addr === null) {
    throw new Error("expected AddressInfo");
  }
  baseUrl = `http://127.0.0.1:${addr.port}`;
});

afterAll(async () => {
  await serverHandle.stop();
});

describe("http server", () => {
  it("GET /health returns 200 + JSON envelope", async () => {
    const res = await fetch(`${baseUrl}/health`);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.status).toBe("ok");
    expect(body.service).toBe("enma-gateway");
    expect(res.headers.get("X-Request-ID")).toBeTruthy();
  });

  it("echoes the X-Request-ID header when supplied", async () => {
    const res = await fetch(`${baseUrl}/health`, {
      headers: { "X-Request-ID": "echo-me-123" },
    });
    expect(res.headers.get("X-Request-ID")).toBe("echo-me-123");
  });

  it("unknown route returns 404 envelope", async () => {
    const res = await fetch(`${baseUrl}/__nope`);
    expect(res.status).toBe(404);
    const body = await res.json();
    expect(body.error.code).toBe("http_404");
  });
});
