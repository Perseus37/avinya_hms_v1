import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig(({ mode }) => {
  const rootEnv = loadEnv(mode, "..", "");
  const proxyTarget = process.env.VITE_API_PROXY_TARGET || rootEnv.VITE_API_PROXY_TARGET;
  if (mode !== "production" && !proxyTarget) {
    throw new Error("VITE_API_PROXY_TARGET must be configured for local development.");
  }

  return {
    plugins: [react(), tailwindcss()],
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