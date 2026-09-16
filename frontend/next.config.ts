import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Standalone output keeps the runtime image small — it ships only the
  // traced dependencies, not the whole node_modules tree.
  output: "standalone",
  // The API is a separate service (FastAPI). In development we proxy through
  // Next so the browser sees one origin and no CORS preflight on every capture
  // action — a supervisor's tablet on a weak plant network should not pay for
  // an extra round trip to log a stop.
  async rewrites() {
    const api = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8080";
    return [{ source: "/api/v1/:path*", destination: `${api}/api/v1/:path*` }];
  },
};

export default nextConfig;
