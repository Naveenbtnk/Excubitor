import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

/**
 * Unit tests for the browser-side code: the SSE parser in `lib/stream.ts` and the Sandbox
 * page. Deliberately separate from Next's build -- these run in jsdom with the same `@` alias
 * the app uses, and nothing here touches the network or a real provider.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.test.{ts,tsx}"],
    // `.next` holds compiled copies of the same sources; excluding it keeps a stale build from
    // being collected as a duplicate test file.
    exclude: ["node_modules/**", ".next/**"],
  },
});
