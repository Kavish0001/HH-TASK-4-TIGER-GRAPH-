import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // ESLint is not installed in this package; type checking still runs in the build.
  eslint: { ignoreDuringBuilds: true },
  // Keeps the Next dev badge out of demo screenshots.
  devIndicators: false,
};

export default nextConfig;
