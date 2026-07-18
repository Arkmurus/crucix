// R-F2731 — test network guard. Prospector 2026-07-18 #3: the global `node --test` never
// terminated AND several tests silently hit LIVE services (e.g. a briefing test POSTed real
// signals to the production brain). Tests must NOT touch the network.
//
// Installed via `--import ./test/helpers/net_guard.mjs` (see `npm run test:offline`), this
// replaces global.fetch with a guard that THROWS a clear, URL-naming error the moment a test
// tries a live call — so an un-mocked test fails LOUDLY instead of hitting prod. A test mocks
// fetch normally (`global.fetch = ...`) to override the guard for its own cases; restoring to
// the captured original restores the guard, preserving isolation.
//
// Escape hatch for a genuine, intentional integration test:
//   import { allowRealNetwork } from './helpers/net_guard.mjs'; allowRealNetwork();  // that test only

const _realFetch = globalThis.fetch;
globalThis.__realFetch = _realFetch;

function _blocked(url) {
  const u = String(url && url.url ? url.url : url).slice(0, 140);
  throw new Error(
    `[net_guard] LIVE network blocked in tests: fetch(${u}). ` +
    `Mock global.fetch in this test (test/helpers/net_guard.mjs), or call allowRealNetwork() ` +
    `for a deliberate integration test.`,
  );
}

globalThis.fetch = async (url) => _blocked(url);

export function allowRealNetwork() {
  globalThis.fetch = _realFetch;
}

// R-F2739 — local capability tests deliberately boot an isolated server. Give
// them a narrow escape hatch that can never reach production or the LAN.
export function allowLoopbackNetwork() {
  globalThis.fetch = async (url, options) => {
    const parsed = new URL(String(url && url.url ? url.url : url));
    if (!['127.0.0.1', 'localhost', '[::1]', '::1'].includes(parsed.hostname)) return _blocked(url);
    return _realFetch(url, options);
  };
}

export function blockRealNetwork() {
  globalThis.fetch = async (url) => _blocked(url);
}
