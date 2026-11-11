/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // SEAM: the engine / data API and agent service run as separate processes.
  // When deployed, these env vars point at those services. See .env.example.
  env: {
    DATA_API_URL: process.env.DATA_API_URL,
    AGENT_SERVICE_URL: process.env.AGENT_SERVICE_URL,
  },
  experimental: {
    // Route handlers and server components may stream from the agent service.
    serverComponentsExternalPackages: [],
  },
};

export default nextConfig;
