import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // ESLint is not installed in this package; type checking still runs in the build.
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;
