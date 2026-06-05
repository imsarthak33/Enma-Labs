import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

let logger;
let withContext;
let logSpy;
let errSpy;

beforeEach(async () => {
  process.env.NODE_ENV = "test";
  process.env.LOG_LEVEL = "debug";
  vi.resetModules();
  const mod = await import("../src/utils/logger.js");
  logger = mod.logger;
  withContext = mod.withContext;
  logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
  errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  logSpy.mockRestore();
  errSpy.mockRestore();
});

/** @param {ReturnType<typeof vi.spyOn>} spy */
function lastJson(spy) {
  const calls = spy.mock.calls;
  expect(calls.length).toBeGreaterThan(0);
  return JSON.parse(calls.at(-1)[0]);
}

describe("logger", () => {
  it("info goes to stdout with structured fields", () => {
    logger.info("hello", { request_id: "r1" });
    const e = lastJson(logSpy);
    expect(e.level).toBe("info");
    expect(e.msg).toBe("hello");
    expect(e.request_id).toBe("r1");
    expect(e.service).toBe("enma-gateway");
    expect(e.ts).toMatch(/T.*Z$/);
  });

  it("warn/error go to stderr", () => {
    logger.warn("careful");
    logger.error("oops");
    expect(errSpy).toHaveBeenCalledTimes(2);
    expect(lastJson(errSpy).level).toBe("error");
  });

  it("respects LOG_LEVEL filter", async () => {
    process.env.LOG_LEVEL = "warn";
    vi.resetModules();
    logSpy.mockReset();
    errSpy.mockReset();
    const { logger: l } = await import("../src/utils/logger.js");
    l.debug("nope");
    l.info("nope");
    l.warn("yes");
    expect(logSpy).not.toHaveBeenCalled();
    expect(errSpy).toHaveBeenCalledTimes(1);
  });

  it("withContext merges bound fields", () => {
    const l = withContext({ request_id: "rX", route: "/health" });
    l.info("hit");
    const e = lastJson(logSpy);
    expect(e.request_id).toBe("rX");
    expect(e.route).toBe("/health");
  });
});
