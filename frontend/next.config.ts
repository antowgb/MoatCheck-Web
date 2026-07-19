import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Static export: the build produces `out/`, served by FastAPI in prod.
  output: "export",
  // Generates screener/index.html etc. so StaticFiles(html=True) resolves /screener/
  trailingSlash: true,
};

export default nextConfig;
