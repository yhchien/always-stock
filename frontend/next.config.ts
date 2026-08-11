import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Reduce duplicate client-side requests in local dev; App Router + Strict Mode
  // can intentionally double-invoke effects, which is especially noisy with SQLite.
  reactStrictMode: false,
  async redirects() {
    return [
      // 2026-08-11：正式推薦頁併入魚尾單一入口；舊書籤/歷史紀錄導向追蹤紀錄頁
      {
        source: "/signals/recommendations",
        destination: "/signals/archive",
        permanent: false,
      },
    ];
  },
};

export default nextConfig;
