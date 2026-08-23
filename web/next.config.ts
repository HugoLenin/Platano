import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The landing page is hand-written static HTML, not a React route, and it
  // lives in public/. Serving it at / keeps one deployment and one URL for the
  // marketing page and the dispatcher console.
  async rewrites() {
    return [{ source: "/", destination: "/landing.html" }];
  },

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
