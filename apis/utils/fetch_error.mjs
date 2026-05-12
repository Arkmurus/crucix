// apis/utils/fetch_error.mjs
//
// R-F369 (2026-05-12) — shared fetch-error unwrap. Mirror of the R-F353
// pattern from apis/sources/intel-feeds.mjs (Comtrade), promoted to a
// shared helper so every source whose catch block previously read
// `err.message` (bare `fetch failed` for undici TypeErrors) can surface
// the actual ENOTFOUND / ECONNRESET / UND_ERR_CONNECT_TIMEOUT / etc.
// errno without a one-off rewrite per file.
//
// Live evidence 2026-05-12 11:26:43 BST seenode logs: World Bank, EU TED
// v3, ransomwatch, NVD, UK Export Controls all logged bare `fetch failed`
// while simultaneously the Comtrade source (already on R-F353) cleanly
// logged `fetch_ENOTFOUND`. The other sources predate R-F353 and still
// emit the un-enriched undici message.
//
// Usage:
//   import { enrichFetchError } from '../utils/fetch_error.mjs';
//   ...
//   } catch (err) {
//     console.warn(`[X] failed: ${enrichFetchError(err)}`);
//   }
//
// Output: e.g. "ENOTFOUND" / "ECONNRESET" / "UND_ERR_CONNECT_TIMEOUT" /
// "TypeError: fetch failed" / "HTTP 500" / original message — whichever
// is more specific. Always returns a string.

export function enrichFetchError(err) {
  if (!err) return 'unknown';
  const cause = (err.cause && typeof err.cause === 'object') ? err.cause : {};
  const detail = cause.code
    || cause.errno
    || err.code
    || err.name
    || err.message
    || 'unknown';
  return String(detail).slice(0, 80);
}
