import { defineConfig } from "vite";
export default defineConfig({
  server: {
    host: "127.0.0.1",
    port: 5186,
    strictPort: true,
    proxy: {
      // Keep the browser's Host header: the backend's same-origin check compares it with Origin.
      "/api": { target: "http://127.0.0.1:8786", changeOrigin: false },
      "/health": { target: "http://127.0.0.1:8786", changeOrigin: false },
    },
  },
  build: { target: "es2022", emptyOutDir: true },
});
