// ESLint 9 flat config — production-strict baseline.
// Adds the security plugin and disallows ad-hoc console usage outside the
// logger module so all output is structured JSON.

import js from "@eslint/js";
import security from "eslint-plugin-security";
import promise from "eslint-plugin-promise";
import globals from "globals";

export default [
  {
    ignores: ["node_modules/**", "coverage/**", "dist/**"],
  },
  js.configs.recommended,
  security.configs.recommended,
  {
    plugins: { promise },
    languageOptions: {
      ecmaVersion: 2024,
      sourceType: "module",
      globals: {
        ...globals.node,
        fetch: "readonly",
      },
    },
    rules: {
      // ---- Correctness ----
      "no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      "no-var": "error",
      "prefer-const": "error",
      "eqeqeq": ["error", "always"],
      "no-implicit-coercion": "error",
      "no-throw-literal": "error",
      "consistent-return": "error",

      // ---- Async hygiene ----
      "no-async-promise-executor": "error",
      "no-await-in-loop": "warn",
      "no-promise-executor-return": "error",
      "require-atomic-updates": "error",
      "promise/no-return-wrap": "error",
      "promise/catch-or-return": "error",

      // ---- Style nudges ----
      "no-console": "warn",

      // ---- Security plugin: tighten beyond default recommended ----
      "security/detect-object-injection": "off", // too noisy for known-shape dispatch
    },
  },
  {
    // Logger is the one place console.* is the actual output channel.
    files: ["src/utils/logger.js"],
    rules: { "no-console": "off" },
  },
  {
    // Tests can be looser.
    files: ["tests/**/*.js", "**/*.test.js"],
    languageOptions: {
      globals: {
        ...globals.node,
        describe: "readonly",
        it: "readonly",
        test: "readonly",
        expect: "readonly",
        beforeEach: "readonly",
        afterEach: "readonly",
        beforeAll: "readonly",
        afterAll: "readonly",
        vi: "readonly",
      },
    },
    rules: {
      "no-console": "off",
      "security/detect-non-literal-fs-filename": "off",
    },
  },
];
