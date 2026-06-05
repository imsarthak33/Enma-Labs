import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    globals: false,
    include: ["tests/**/*.test.js", "src/**/*.test.js"],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov", "html"],
      include: ["src/**/*.js"],
      exclude: ["src/index.js", "**/*.test.js"],
      thresholds: {
        lines: 80,
        functions: 80,
        statements: 80,
        branches: 70,
      },
    },
    reporters: process.env.CI ? ["default", "junit"] : ["default"],
    outputFile: { junit: "coverage/junit.xml" },
  },
});
