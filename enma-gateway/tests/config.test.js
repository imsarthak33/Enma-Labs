import { describe, it, expect, beforeEach, afterEach } from "vitest";

const ENV_KEYS = [
  "NODE_ENV",
  "LOG_LEVEL",
  "PORT",
  "TELEGRAM_BOT_TOKEN",
  "BACKEND_URL",
  "BACKEND_API_KEY",
  "GATEWAY_HMAC_SECRET",
  "SENTRY_DSN",
];

/**
 * Reset the modules so `import("../src/config.js")` re-evaluates with the
 * current process.env. Vitest caches ESM modules per worker — `vi.resetModules`
 * gives us a clean import each test.
 */
async function freshConfig() {
  const { vi } = await import("vitest");
  vi.resetModules();
  return import("../src/config.js");
}

describe("config", () => {
  /** @type {Record<string, string | undefined>} */
  let snapshot;

  beforeEach(() => {
    snapshot = {};
    for (const k of ENV_KEYS) snapshot[k] = process.env[k];
  });

  afterEach(() => {
    for (const k of ENV_KEYS) {
      if (snapshot[k] === undefined) delete process.env[k];
      else process.env[k] = snapshot[k];
    }
  });

  it("loads sensible development defaults", async () => {
    process.env.NODE_ENV = "development";
    delete process.env.PORT;
    delete process.env.TELEGRAM_BOT_TOKEN;

    const { config } = await freshConfig();
    expect(config.env).toBe("development");
    expect(config.port).toBe(3000);
    expect(config.logLevel).toBe("debug");
    expect(config.service.name).toBe("enma-gateway");
  });

  it("rejects an invalid NODE_ENV", async () => {
    process.env.NODE_ENV = "playground";
    await expect(freshConfig()).rejects.toThrow(/NODE_ENV/);
  });

  it("rejects a non-numeric PORT", async () => {
    process.env.NODE_ENV = "development";
    process.env.PORT = "not-a-port";
    await expect(freshConfig()).rejects.toThrow(/PORT/);
  });

  it("requires TELEGRAM_BOT_TOKEN in production", async () => {
    process.env.NODE_ENV = "production";
    delete process.env.TELEGRAM_BOT_TOKEN;
    process.env.BACKEND_URL = "http://backend:8000";
    process.env.BACKEND_API_KEY = "k";
    process.env.GATEWAY_HMAC_SECRET = "s";
    await expect(freshConfig()).rejects.toThrow(/TELEGRAM_BOT_TOKEN/);
  });

  it("safeConfigSnapshot redacts secrets", async () => {
    process.env.NODE_ENV = "development";
    process.env.TELEGRAM_BOT_TOKEN = "real-token-shhh";
    process.env.BACKEND_API_KEY = "real-key";
    process.env.GATEWAY_HMAC_SECRET = "real-secret";

    const { safeConfigSnapshot } = await freshConfig();
    const snap = safeConfigSnapshot();
    expect(snap.telegram_bot_token).toBe("***");
    expect(snap.backend_api_key).toBe("***");
    expect(snap.gateway_hmac_secret).toBe("***");
    expect(JSON.stringify(snap)).not.toContain("real-token-shhh");
  });
});
