import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Report viewer links are one-shot and scoped; never let a CDN cache them.
  async headers() {
    return [
      {
        source: "/r/:token*",
        headers: [
          { key: "Cache-Control", value: "no-store, max-age=0" },
          { key: "X-Robots-Tag", value: "noindex, nofollow, noarchive" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};

export default nextConfig;
