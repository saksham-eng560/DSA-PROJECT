/** @type {import('next').NextConfig} */
const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  poweredByHeader: false,
  experimental: {
    // A local AI model (Ollama) without a GPU can take minutes to answer (resume parsing, Test AI);
    // Next's default 30 s proxy timeout would cut those /api requests off.
    proxyTimeout: 15 * 60 * 1000,
  },
  // Same-origin proxy (BFF): the browser only talks to the dashboard's origin, so the
  // httpOnly session cookie is first-party in every deployment topology.
  async rewrites() {
    return [
      { source: "/api/v1/:path*", destination: `${BACKEND_URL}/api/v1/:path*` },
      { source: "/docs", destination: `${BACKEND_URL}/docs` },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "SAMEORIGIN" },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
