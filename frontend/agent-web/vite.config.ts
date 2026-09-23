import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  base: "/runtime/",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5174,
    strictPort: true,
    proxy: {
      "/api": process.env.INTENT_API_PROXY ?? "http://127.0.0.1:8000",
      "/health": process.env.INTENT_API_PROXY ?? "http://127.0.0.1:8000",
    },
    fs: { allow: [fileURLToPath(new URL("..", import.meta.url))] },
  },
  build: { outDir: "../dist/agent-web", emptyOutDir: true },
});
