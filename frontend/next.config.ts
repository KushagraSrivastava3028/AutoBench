import type { NextConfig } from "next";

const API = process.env.AUTOBENCH_API_URL ?? "http://localhost:8000";

const config: NextConfig = {
  reactStrictMode: true,
  // Pin the trace root: without it Next walks up and finds an unrelated
  // lockfile in the home directory, then traces the wrong tree.
  outputFileTracingRoot: import.meta.dirname,
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: [
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "X-Frame-Options", value: "DENY" },
      { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
      { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
    ] }];
  },
  // Proxy the API through Next in development so the browser makes same-origin
  // requests: no CORS preflight, and SSE survives without credential juggling.
  async rewrites() {
    return [
      { source: "/healthz", destination: `${API}/healthz` },
    ];
  },
};

export default config;
