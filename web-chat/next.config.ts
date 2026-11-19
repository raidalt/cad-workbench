import type { NextConfig } from "next";
import { fileURLToPath } from "node:url";

const workspaceRoot = fileURLToPath(new URL("../", import.meta.url));

// Static export served by the Python `cad serve` process from
// src/cad/web/chat/ at /static/chat/. No Node at runtime, no route handlers.
const nextConfig: NextConfig = {
  output: "export",
  basePath: "/static/chat",
  assetPrefix: "/static/chat/",
  trailingSlash: true,
  images: { unoptimized: true },
  // The export is copied to ../src/cad/web/chat by scripts/sync-export.mjs.
  distDir: ".next",
  turbopack: {
    root: workspaceRoot,
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
