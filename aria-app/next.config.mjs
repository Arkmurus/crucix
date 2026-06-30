/** @type {import('next').NextConfig} */

// Backend (server.mjs) base. Baked at build for the API proxy (rewrites are compiled
// into the routes manifest, so this is read at BUILD time):
//   - fly:   http://aria-web.internal:3117  (stable 6PN address, non-secret) via build arg
//   - local: http://localhost:3117          (.env.local)
const BACKEND_URL = process.env.BACKEND_URL || 'http://aria-web.internal:3117';

const nextConfig = {
  // Standalone output keeps the fly image small (server.js + minimal node_modules).
  output: 'standalone',
  reactStrictMode: true,
  async rewrites() {
    // Proxy API calls to the existing backend so the browser stays SAME-ORIGIN and
    // never learns the backend host. These are afterFiles rewrites: the local
    // /api/session route handler is matched first; everything else under /api/*
    // (auth, aria, billing, data, ...) is forwarded to server.mjs.
    return [{ source: '/api/:path*', destination: `${BACKEND_URL}/api/:path*` }];
  },
};

export default nextConfig;
