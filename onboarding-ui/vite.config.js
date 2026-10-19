import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import tailwindcss from "@tailwindcss/vite";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

export default defineConfig(({ mode }) => {
  const projectRoot = fileURLToPath(new URL(".", import.meta.url));
  const rootEnv = loadEnv(mode, "..", "");
  const proxyTarget = process.env.VITE_API_PROXY_TARGET || rootEnv.VITE_API_PROXY_TARGET;
  if (mode !== "production" && !proxyTarget) {
    throw new Error("VITE_API_PROXY_TARGET must be configured for local development.");
  }

  return {
    plugins: [react(), tailwindcss()],
    build: {
      rollupOptions: {
        input: {
          app: resolve(projectRoot, "index.html"),
          legacyConsole: resolve(projectRoot, "src/legacy-console.css"),
        },
        output: {
          assetFileNames: (assetInfo) =>
            assetInfo.name === "legacyConsole.css"
              ? "assets/legacy-console.css"
              : "assets/[name]-[hash][extname]",
        },
      },
    },
    server: mode === "production" ? undefined : {
      proxy: {
        "/api": {
          target: proxyTarget,
          changeOrigin: true,
        },
      },
    },
  };
});