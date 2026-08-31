import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const backendTarget = "http://localhost:8642";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
  },
  server: {
    proxy: {
      "/api": {
        target: backendTarget,
        changeOrigin: true,
      },
      "/data": {
        target: backendTarget,
        changeOrigin: true,
      },
    },
  },
});
