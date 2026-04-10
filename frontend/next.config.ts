import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Reduce duplicate client-side requests in local dev; App Router + Strict Mode
  // can intentionally double-invoke effects, which is especially noisy with SQLite.
  reactStrictMode: false,
};

export default nextConfig;
