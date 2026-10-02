import type { NextConfig } from "next";

const backend = process.env.CATALOGGUARD_API_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  reactStrictMode: true,
  // Server components fetch the API directly, so the browser never needs a
  // cross-origin request against the backend and no CORS surface is opened up
  // for the dashboard itself.
  env: { NEXT_PUBLIC_API_URL: backend },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default config;
