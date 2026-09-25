import type { NextConfig } from "next";

// The browser only ever talks to this origin; /api/* is forwarded to the
// FastAPI service, so the session cookie stays first-party.
const API_URL = process.env.RECOVA_API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/:path*` }];
  },
};

export default nextConfig;
