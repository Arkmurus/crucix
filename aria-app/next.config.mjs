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
    // FRONT-DOOR / strangler proxy (R-F2175): aria-app is the public entry for
    // intel.arkmurus.com. These are afterFiles rewrites — aria-app's own filesystem
    // routes (the migrated NEW-design pages: /signin /dashboard /reports /opportunities
    // /watchlist /vault /account /chat /admin /support, plus /api/session and the
    // /api/aria/chat/stream bridge, and /_next/*) ALWAYS win. EVERYTHING ELSE falls
    // through to the existing backend (server.mjs): unmigrated pages, marketing/legal/
    // signup/recovery, all other /api/*, static assets, /healthz — so nothing breaks
    // during the page-by-page migration. As pages are rebuilt they move from
    // proxied-to-aria-web to served-by-aria-app automatically (filesystem precedence).
    // NOTE: Stripe/Telegram webhooks should target aria-web.fly.dev directly (not the
    // proxied domain) to keep raw-body signature verification untouched.
    return [{ source: '/:path*', destination: `${BACKEND_URL}/:path*` }];
  },
};

export default nextConfig;
