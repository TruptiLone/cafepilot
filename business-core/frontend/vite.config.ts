import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

declare const process: { env: Record<string, string | undefined> }; // Node's, without adding @types/node

// API_PROXY_TARGET points the dev server at another backend, e.g. a worktree's on port 8011:
//   API_PROXY_TARGET=http://127.0.0.1:8011 npm run dev -- --port 5183
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.API_PROXY_TARGET || "http://127.0.0.1:8001",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
  },
});
