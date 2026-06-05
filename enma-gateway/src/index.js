// =============================================================================
// enma-gateway — process entrypoint.
//
// Responsibilities:
//   1. Validate config (config.js throws on misconfig).
//   2. Start the HTTP server (health endpoint).
//   3. Initialise the message router + media-group buffer.
//   4. Start the Telegram long-polling loop.
//   5. Install graceful-shutdown handlers.
// =============================================================================

import { config, safeConfigSnapshot } from "./config.js";
import { logger } from "./utils/logger.js";
import { startServer } from "./server.js";
import { initRouter, routeUpdate, getBuffer } from "./routing/message_router.js";
import { startPoller } from "./polling/telegram_poller.js";

async function main() {
  logger.info("boot", { config: safeConfigSnapshot() });

  // 1. HTTP server (health, metrics).
  const { stop: stopServer } = startServer(config.port);

  // 2. Message router + media-group buffer.
  initRouter();

  // 3. Telegram long-polling.
  const { stop: stopPoller } = startPoller({ routeFn: routeUpdate });

  // Shutdown task list — executed in order on SIGINT / SIGTERM.
  const shutdownTasks = [
    stopPoller,
    () => {
      getBuffer().clear();
    },
    stopServer,
  ];

  const shutdown = async (signal) => {
    logger.warn("shutdown_signal", { signal });
    let exitCode = 0;
    for (const task of shutdownTasks) {
      try {
        await task();
      } catch (err) {
        logger.error("shutdown_task_failed", { error: String(err) });
        exitCode = 1;
      }
    }
    logger.info("shutdown_complete", { exitCode });
    process.exit(exitCode);
  };

  process.on("SIGINT", () => void shutdown("SIGINT"));
  process.on("SIGTERM", () => void shutdown("SIGTERM"));

  process.on("uncaughtException", (err) => {
    logger.error("uncaught_exception", { error: String(err), stack: err.stack });
    process.exit(1);
  });

  process.on("unhandledRejection", (reason) => {
    logger.error("unhandled_rejection", { reason: String(reason) });
    process.exit(1);
  });
}

main().catch((err) => {
  logger.error("boot_failed", { error: String(err), stack: err.stack });
  process.exit(1);
});
