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
    // ROLLBACK MODE (R-F2187): the thin P1 pages are below parity, so aria-app is a
    // TRANSPARENT pass-through — `beforeFiles` proxies EVERY path to the old platform
    // (server.mjs / aria-web) BEFORE any aria-app filesystem route, so intel.arkmurus.com
    // serves the full mature platform again. No DNS change needed.
    //
    // REBUILD: as each page is rebuilt on the boilerplate to parity, move it from here
    // to `afterFiles` (or remove its beforeFiles entry) so aria-app serves the NEW page
    // and everything else keeps proxying to aria-web (strangler migration).
    // Proxy EVERYTHING to aria-web EXCEPT /preview/*, the app-owned commit-verifiable
    // health endpoint, and Next's own /_next assets, so
    // new boilerplate-grade pages can be reviewed live at intel.arkmurus.com/preview/*
    // while the full old platform stays the default everywhere else.
    return {
      beforeFiles: [
        { source: '/:path((?!preview/|preview$|signin$|api/session$|dashboard$|reports(?:/|$)|health/app$|_next/).*)', destination: `${BACKEND_URL}/:path` },
      ],
      afterFiles: [],
      fallback: [],
    };
  },
};

export default nextConfig;
