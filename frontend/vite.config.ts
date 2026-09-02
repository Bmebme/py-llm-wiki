import path from "path"
import { readFileSync } from "fs"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

// Read version from package.json at config-load time so the Settings
// UI can show the running app version without duplicating the string.
const pkgJson = JSON.parse(readFileSync(path.resolve(__dirname, "package.json"), "utf-8"))

// Browser port of the Tauri shell: the @tauri-apps imports that the
// desktop app used now resolve to local shims (src/vendor/tauri/*).
// src/vendor/tauri/core.ts relays invoke() to the FastAPI backend.
const vendor = (name: string) => path.resolve(__dirname, `./src/vendor/tauri/${name}.ts`)

// https://vitejs.dev/config/
export default defineConfig(() => ({
  plugins: [react(), tailwindcss()],

  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      "@tauri-apps/api/core": vendor("core"),
      "@tauri-apps/api/event": vendor("event"),
      "@tauri-apps/api/window": vendor("api-window"),
      "@tauri-apps/plugin-store": vendor("plugin-store"),
      "@tauri-apps/plugin-http": vendor("plugin-http"),
      "@tauri-apps/plugin-opener": vendor("plugin-opener"),
      "@tauri-apps/plugin-dialog": vendor("plugin-dialog"),
      "@tauri-apps/plugin-autostart": vendor("plugin-autostart"),
    },
  },

  define: {
    __APP_VERSION__: JSON.stringify(pkgJson.version),
  },

  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    // Same-origin proxy to the FastAPI backend: no CORS anywhere in
    // dev, and SSE streams survive the proxy.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:19828",
        changeOrigin: true,
      },
    },
  },

  test: {
    environment: "node",
    // Loads .env.test.local into process.env for real-LLM tests.
    // The loader itself is a no-op if the file is absent, so this is
    // safe to keep on for every test run.
    setupFiles: ["./src/test-helpers/load-test-env.ts"],
  },
}))
