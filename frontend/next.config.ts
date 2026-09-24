import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // ESLint is not installed in this package; type checking still runs in the build.
  eslint: { ignoreDuringBuilds: true },
  // Keeps the Next dev badge out of demo screenshots.
  devIndicators: false,
  // Browsers still ask for /favicon.ico; serve the SVG mark instead of a 404.
  async rewrites() {
    return [{ source: "/favicon.ico", destination: "/mark.svg" }];
  },
};

export default nextConfig;
