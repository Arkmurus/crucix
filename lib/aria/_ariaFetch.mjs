// Shared HTTP helper for seenode → Python-brain (fly.io) calls.
//
// Past gap (verified 2026-04-19 00:11 via meta_query): seenode posted
// emails to /api/aria/read-document with NO Authorization header. The
// Python router protects all /api/aria/* endpoints with a bearer-token
// dependency, so every call returned 401 and was silently swallowed by
// the fire-and-forget catch. 302 emails read by seenode → 0 RAG chunks
// indexed → ARIA "absorbed 1 email" forever.
//
// This module centralises the auth + URL resolution so every Node-side
// caller (emailReader, waListener, learning_store, future workers)
// uses the same logic and the same token-fallback chain. Drop-in
// replacement for global fetch() when targeting the Python brain.
//
// Token fallback (matches Python a0a1d35 — accepts EITHER token):
//   1. ARIA_API_TOKEN  (canonical user-facing API token)
//   2. ARIA_INTERNAL_TOKEN  (internal-service token; what seenode has)
//
// URL resolution (matches lib/self/learning_store.mjs b7c89a4):
//   1. Caller passes absolute URL → use as-is
//   2. Caller passes path starting with /api/ → prepend ARIA_SERVICE_URL
//   3. Caller passes path starting with / → prepend ARIA_SERVICE_URL

const TOKEN = (
  process.env.ARIA_API_TOKEN ||
  process.env.ARIA_INTERNAL_TOKEN ||
  ''
).trim();

const BASE_URL = (
  process.env.ARIA_SERVICE_URL ||
  process.env.BRAIN_SERVICE_URL ||
  process.env.ARIA_BRAIN_URL ||
  ''
).replace(/\/+$/, '');

let _ariaFetchWarned = false;

/**
 * Authenticated fetch to the Python brain (fly.io aria-intel).
 * Adds Authorization: Bearer <token> when a token is configured.
 *
 * @param {string} urlOrPath  Absolute URL OR path starting with /api/...
 * @param {object} opts       Standard fetch() options
 * @returns {Promise<Response>}
 */
export function _ariaFetch(urlOrPath, opts = {}) {
  let url = urlOrPath;
  if (urlOrPath.startsWith('/')) {
    if (!BASE_URL) {
      if (!_ariaFetchWarned) {
        _ariaFetchWarned = true;
        console.warn(
          '[_ariaFetch] No ARIA_SERVICE_URL set — cannot resolve relative ' +
          'path ' + urlOrPath + '. Set ARIA_SERVICE_URL=https://aria-intel.fly.dev'
        );
      }
      // Fall back to localhost; will fail loudly per-call below.
      url = 'http://localhost:8000' + urlOrPath;
    } else {
      url = BASE_URL + urlOrPath;
    }
  }

  const headers = { ...(opts.headers || {}) };
  if (TOKEN && !headers['Authorization'] && !headers['authorization']) {
    headers['Authorization'] = 'Bearer ' + TOKEN;
  }

  return fetch(url, { ...opts, headers });
}

export { _ariaFetch as ariaFetch };
export default _ariaFetch;
