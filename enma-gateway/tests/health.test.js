import { describe, it, expect, beforeAll } from "vitest";

describe("health payload", () => {
  /** @type {(typeof import("../src/utils/health.js"))} */
  let mod;

  beforeAll(async () => {
    process.env.NODE_ENV = "test";
    mod = await import("../src/utils/health.js");
  });

  it("returns the expected envelope", () => {
    const p = mod.buildHealthPayload();
    expect(p.status).toBe("ok");
    expect(p.service).toBe("enma-gateway");
    expect(p.version).toMatch(/^\d+\.\d+\.\d+$/);
    expect(typeof p.uptime_s).toBe("number");
    expect(p.uptime_s).toBeGreaterThanOrEqual(0);
    expect(["development", "staging", "production", "test"]).toContain(p.env);
  });

  it("uptime is monotonically non-decreasing", async () => {
    const a = mod.buildHealthPayload().uptime_s;
    await new Promise((r) => setTimeout(r, 10));
    const b = mod.buildHealthPayload().uptime_s;
    expect(b).toBeGreaterThanOrEqual(a);
  });
});
