/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  typescript: {
    // Allow production builds even with type errors (we validate separately)
    ignoreBuildErrors: false,
  },
  eslint: {
    // Disable eslint during builds
    ignoreDuringBuilds: true,
  },
  images: {
    // Allow images from any domain (for generated placeholder images)
    remotePatterns: [
      {
        protocol: "https",
        hostname: "**",
      },
      {
        protocol: "http",
        hostname: "**",
      },
    ],
    // Also allow unoptimized for placeholder.svg etc
    unoptimized: true,
  },
};

export default nextConfig;
