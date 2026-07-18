// lib/billing/enforce.mjs
// R-F2765 — per-tier quota enforcement decision, factored out of server.mjs so
// the LOAD-BEARING exemption is unit-testable in isolation (server.mjs is a
// 7k-line side-effecting module that can't be imported into a fast test).
//
// Background: the tiers.mjs message/ddRun caps were DEFINED but never CHECKED on
// the main web path — only the public-API surface called checkAndConsume. That
// is a runaway-spend hole: once the primary LLM switches DeepSeek -> Claude
// (~10-50x/token), an unbounded caller can exhaust the $300/mo cap. This closes
// it on the two hot endpoints (chat message, DD orchestrate).
//
// THE EXEMPTION IS LOAD-BEARING. System / internal callers have NO JWT userId:
//   - the WhatsApp listener calls /api/aria/chat with the internal token
//     (requireAuth sets req.user = { id:'aria-internal' } with no userId),
//   - the localhost bypass calls next() with no req.user at all.
// checkAndConsume returns { allowed:false, reason:'no userId' } for a falsy
// userId, so WITHOUT this exemption every internal / WA / localhost call would be
// wrongly 429'd (WhatsApp chat would break). We exempt them by returning null
// (= allowed) whenever there is no userId. This is exactly what the capability
// test asserts.
import { checkAndConsume } from './quotas.mjs';
import { DEFAULT_TIER } from './tiers.mjs';

// enforceQuota(userId, tier, kind)
//   userId : the authenticated JWT user id, or falsy for system/internal callers
//   tier   : the caller's tier id (defaults to free); ignored when exempt
//   kind   : 'message' | 'ddRun' | 'upload'
// Returns:
//   null                       -> allowed OR exempt (no userId) — proceed
//   { allowed:false, current, cap, reason }  -> cap hit; caller returns 429
export async function enforceQuota(userId, tier, kind) {
  if (!userId) return null; // system / internal token / localhost bypass -> exempt (load-bearing)
  const verdict = await checkAndConsume(userId, tier || DEFAULT_TIER, kind);
  return verdict.allowed ? null : verdict;
}
