import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backendTarget = "http://localhost:8642";

export default defineConfig({
  plugins: [react()],
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
