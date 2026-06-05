// =============================================================================
// Minimal native http server for gateway endpoints.
//
// Two endpoints today (Phase 0):
//   GET /health      → liveness JSON
//   GET /metrics     → placeholder; Phase 8 wires Prometheus exposition
//
// Phase 2 will start the Telegram poller in parallel via src/polling/*.
// =============================================================================

import http from "node:http";
import { randomUUID } from "node:crypto";

import { buildHealthPayload } from "./utils/health.js";
import { logger, withContext } from "./utils/logger.js";

const REQUEST_ID_HEADER = "x-request-id";

/**
 * @param {http.IncomingMessage} req
 * @param {http.ServerResponse} res
 */
function handle(req, res) {
  const requestId =
    /** @type {string | undefined} */ (req.headers[REQUEST_ID_HEADER]) ?? randomUUID();
  const log = withContext({ request_id: requestId, method: req.method, path: req.url });

  res.setHeader("X-Request-ID", requestId);
  res.setHeader("Content-Type", "application/json");

  const { method = "GET", url = "/" } = req;
  const pathname = url.split("?")[0];

  try {
    if (method === "GET" && pathname === "/health") {
      res.statusCode = 200;
      res.end(JSON.stringify(buildHealthPayload()));
      log.debug("health_ok");
      return;
    }

    if (method === "GET" && pathname === "/metrics") {
      res.statusCode = 200;
      res.end(JSON.stringify({ status: "todo", phase: "0" }));
      return;
    }

    res.statusCode = 404;
    res.end(
      JSON.stringify({
        error: { code: "http_404", message: `No route for ${method} ${pathname}` },
      }),
    );
  } catch (err) {
    log.error("unhandled_handler_error", { error: String(err) });
    if (!res.headersSent) {
      res.statusCode = 500;
      res.end(
        JSON.stringify({ error: { code: "internal_error", message: "Internal error" } }),
      );
    }
  }
}

/**
 * @param {number} port
 * @returns {{ server: http.Server, stop: () => Promise<void> }}
 */
export function startServer(port) {
  const server = http.createServer(handle);
  server.listen(port, () => {
    logger.info("server_listening", { port });
  });

  const stop = () =>
    new Promise((resolve, reject) => {
      server.close((err) => {
        if (err) return reject(err);
        resolve();
      });
    });

  return { server, stop };
}

// Exposed for unit tests.
export const _internals = { handle };
