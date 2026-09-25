import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  test: {
    environment: "happy-dom",
    setupFiles: ["./vitest.setup.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      // No thresholds yet — suite is thin (few test files). Add
      // `thresholds: { lines: 95, ... }` once coverage is built out; CI
      // treats this as a soft/visibility-only report until then.
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
