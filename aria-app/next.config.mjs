/** @type {import('next').NextConfig} */
const nextConfig = {
  // Standalone output keeps the fly image small (no node_modules in runtime stage).
  output: 'standalone',
  reactStrictMode: true,
  // aria-app is a pure frontend; all data comes from server.mjs (/api) and the brain (/api/aria).
  // BACKEND_URL is read server-side (SSR/middleware); NEXT_PUBLIC_BACKEND_URL for the browser.
  env: {
    NEXT_PUBLIC_BACKEND_URL: process.env.NEXT_PUBLIC_BACKEND_URL || '',
  },
};

export default nextConfig;
