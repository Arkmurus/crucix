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
  // R-F553 (2026-05-16) — skip err.name when it's the bare base-class
  // string "Error". Live evidence: afdb.mjs throws
  //   new Error('All AfDB endpoints unreachable: rss2json=429 | ...')
  // every sweep when its 5 fallbacks all fail. Pre-R-F553 enrichFetchError
  // hit err.name === 'Error' before err.message and returned the literal
  // "Error", producing the unhelpful seenode log line `[AfDB] Error: Error`
  // 3 of 4 sweeps in the 2026-05-16 morning log. TypeError / AbortError /
  // DOMException etc. stay preferred since their class name IS the
  // diagnostic (R-F369 rationale). Only the base "Error" sentinel is
  // demoted — for it, the message text is strictly more informative.
  const nameDetail = (err.name && err.name !== 'Error') ? err.name : null;
  const detail = cause.code
    || cause.errno
    || err.code
    || nameDetail
    || err.message
    || err.name
    || 'unknown';
  return String(detail).slice(0, 80);
}
