// lib/auth/infraRoutes.mjs
// R-F2775: canonical classification of which /api/aria/* paths are OPERATOR/INFRA
// surface (role-gated) vs CUSTOMER surface (never gated). Lives in its own module —
// like lib/auth/roles.mjs (R-F2170/R-F2773) — so tests can assert the classification
// without booting server.mjs.
//
// ── Why a path ALLOWLIST and not a prefix sweep ──────────────────────────────
// The audit's original sketch was "gate the infra prefixes before the catch-all".
// Two facts killed that shape:
//
//  1. ORDERING. The explicit /api/aria/* routes are registered at server.mjs
//     ~2848-3760; the catch-all is at ~6027. Express matches in registration
//     order, so a gate mounted before the catch-all fires ONLY for paths that no
//     explicit route claimed — i.e. it would have protected almost nothing it was
//     meant to protect. The gate is therefore mounted EARLY (right after the
//     R-F577 public model-card endpoints at ~1437), where it sees every
//     /api/aria/* request before the explicit handlers below it.
//
//  2. MIXED PREFIXES. `/dd` carries both customer (`/dd/reports`, `/dd/watchlist`,
//     `/dd/vault`, `/dd/vls/{key,proof,verify}`) and infra (`/dd/quarantine`,
//     `/dd/vls/chain`) paths; `/rag` carries customer `/rag/{search,ingest}` next
//     to infra `/rag/{stats,sources,backfill}`. Gating either prefix wholesale
//     would 403 paying customers.
//
// So: DEFAULT IS PASS-THROUGH. A path is gated only if it appears below. Anything
// not listed keeps exactly today's behaviour (requireAuth on its own route). That
// makes the failure mode "not yet gated" rather than "customer locked out".
//
// Membership was derived from evidence, not vibes: every endpoint referenced by
// public/**/*.{html,js} was cross-referenced against the R-F2774 operator-page set
// (aria-brain, sources, vls-chain, bd-intelligence, leads, design-partners, vault,
// wa-connections, admin). Only endpoints referenced EXCLUSIVELY by operator pages —
// or by no page at all and unambiguously infra by name — are listed here.

/**
 * Infra path prefixes, relative to the /api/aria mount point (so `/cost/monthly`,
 * not `/api/aria/cost/monthly`). A request matches when its path equals the entry
 * or continues with `/` — `/cost` matches `/cost` and `/cost/monthly`, never
 * `/costcentre`.
 */
export const INFRA_ARIA_PATHS = Object.freeze([
  // ── autonomy / self-improvement ──
  '/autonomy',            // composite, history, baseline, surface
  '/autonomous',          // status, dlq
  '/self',                // code, deploy, staged, rollback, update-log
  '/adversarial',         // run_weekly + amendments/*  (NB: /adversarial/stats is
                          // registered publicly at server.mjs ~1431, BEFORE this
                          // gate, so it stays public — see the ordering note above)
  // ── observability / measurement ──
  '/cost',                // cumulative, external, monthly, recent
  '/metrics',             // grounded_rate, contradiction_rate, fact_decay
  '/predictor',           // block_rate
  '/hallucination',
  '/verification/stats',
  '/unguarded-fallback',
  '/independence',
  '/consistency',         // scores (GET) + run (POST → admin)
  '/calibration',         // review/baseline/auto-tune (GET) + their runs (POST)
  '/diagnostic',          // details (GET) + run (POST)
  '/circuit-breakers',
  '/operating-mode',
  '/pending-actions',
  '/phase',               // phase/gates — Phase A gate readings
  // ── brain internals ──
  '/brain/stats',
  '/brain/alerts',
  '/brain/dashboard',
  '/knowledge/inventory',
  '/rag/stats',           // NOT '/rag' — /rag/search + /rag/ingest are CUSTOMER
  '/rag/sources',
  '/rag/backfill',
  '/reasoning-library',
  '/critique',
  '/rlaif',
  // ── learning / training ──
  '/student',             // mastery, heatmap, stats, curriculum, quiz, study
  '/learning',            // /api/aria/learning/stats (distinct from the customer
                          // /api/learning/stats route, which is NOT under this mount)
  '/training-data',
  // ── source operations ──
  '/sources/uptime',      // uptime (GET) + run/suspend/unsuspend (POST → admin)
  '/sources/seed',        // catalogue (GET) + run (POST → admin)
  '/oem',                 // operator OEM contact book (sources.html)
  // ── DD infra slivers (the rest of /dd is CUSTOMER — do not widen) ──
  '/dd/quarantine',
  '/dd/vls/chain',

  // ── Added by the R-F2775 Pass-2 review. Each was verified against every
  //    public/**/*.{html,js} reference first: none is touched by a customer page.
  '/admin',               // admin/wedge-stacks, admin/logs/*, admin/absorption-quarantine,
                          // admin/airtable-buffer, admin/rebuild-semantic-index, admin/migrate-state
  '/wa-auth',             // returns the Baileys device auth bundle — was viewer-readable.
                          // Highest-value omission found, given the R-F2705 key leak.
  '/self-healing',        // NB: '/self' does NOT cover these — matchesPrefix requires an
  '/self-restart',        // exact match or `prefix + '/'`, so '/self-healing' is a distinct
                          // entry. POST /self-restart/trigger was viewer-callable: a
                          // one-request DoS on a service with a ~10-minute boot (§11c).
  '/mastery',             // R-F677 registered /mastery/heatmap as an ALIAS of
  '/quality',             // /student/mastery/heatmap, so gating '/student' alone moved no
                          // data. Same shape for /quality/grounded-rate vs /metrics/*.
  '/sources/health',      // alias-shaped sibling of the gated /sources/uptime
  '/neural',
  '/metacognitive',
  '/corpus',
  '/ground-truth',
  '/eval',                // incl. POST /eval/golden/freeze — the Phase A gate-#6 pin
  '/coder',
  '/source_validator',
  '/stream-guards',
  '/operator-pending',
  '/harvest',
  '/memory/diagnose',     // NOT '/memory' — keep the prefix narrow in case customer
  '/memory/health',       // memory endpoints appear later
  '/memory/tiers',
  '/security/api-monitor', // NOT '/security' — /security/counter-intel/scan is CUSTOMER
                           // (explorer.html)
  '/trace/recent',        // NOT '/trace' — /trace/:trace_id is CUSTOMER
  '/trace/stats',
]);

const MUTATING = Object.freeze(['POST', 'PUT', 'PATCH', 'DELETE']);

function matchesPrefix(path, prefix) {
  if (path === prefix) return true;
  return path.startsWith(prefix + '/');
}

/**
 * R-F2802 (SECURITY) — true when `path` is STILL percent-encoded after one decode.
 *
 * Double-encoding (`%2573tudent` → `%73tudent` → `student`) would let an attacker
 * step past a single decode. Rather than loop-decoding — which invites its own
 * mismatch with whatever the upstream does — anything still encoded after one
 * pass is treated as hostile and the gate FAILS CLOSED on it. Legitimate ARIA
 * paths never contain a literal `%`.
 */
export function isDoubleEncodedPath(path) {
  const raw = String(path || '').split('?')[0];
  if (!raw.includes('%')) return false;
  let once = raw;
  try { once = decodeURIComponent(raw); } catch { return true; }  // malformed → hostile
  return once.includes('%');
}

/**
 * Normalise a request path into the form the prefix list is written in.
 *
 * R-F2802 — PERCENT-DECODING IS A SECURITY CONTROL. Express's `req.path` is
 * `parseurl(req).pathname`, which is NOT decoded, while uvicorn/Starlette DO
 * unquote before routing. So the gate was comparing the raw encoded string while
 * the brain served the decoded one, and encoding a single character walked
 * straight past the classifier.
 *
 * REPRODUCED live with a viewer token before this fix:
 *   GET /api/aria/%73tudent/mastery/heatmap → 200   (plain form → 403)
 *   GET /api/aria/%63ost/monthly            → 200   (plain form → 403)
 *   GET /api/aria/%77a-auth/backup          → 405   (gate bypassed, route reached)
 * `%77a-auth` is the Baileys device auth bundle — the R-F2705 asset.
 *
 * NB this is why `%2e` appeared harmless in the earlier round: it decodes to `.`,
 * which Express then normalises away, so that ONE case self-healed. `%73` has no
 * such second mechanism. The earlier note drew the right conclusion from the
 * wrong reason — decode explicitly, do not rely on Express.
 *
 * CASE-FOLDING IS A SECURITY CONTROL HERE, not tidiness. Express disables
 * `case sensitive routing` by default, so `app.get('/api/aria/student/mastery/heatmap')`
 * also answers `/api/aria/STUDENT/mastery/heatmap` — and those explicit handlers
 * forward a HARDCODED lowercase path upstream, so the upstream never sees the
 * mangled case either. A case-sensitive `startsWith` therefore missed the infra
 * classification while the route still served the request.
 *
 * REPRODUCED on a live server with a viewer token before this fix:
 *   GET  /api/aria/STUDENT/mastery/heatmap → 200  (lowercase form → 403)
 *   POST /api/aria/SOURCES/uptime/run      → 200  (lowercase form → 403)
 * i.e. a plain customer could both READ infra and TRIGGER an infra mutation by
 * changing the case of one path segment. Fold case before matching.
 *
 * Duplicate slashes are collapsed for the same defence-in-depth reason (`//student`
 * is a distinct string that upstream may or may not treat as equivalent).
 *
 * NB: dot-segments (`/./`, `%2e`) were TESTED and need no handling — Express
 * normalises them out before `req.path`, and the gate correctly returned 403 for
 * `/api/aria/./cost/monthly` and `/api/aria/%2e/cost/monthly`. Do not add
 * speculative handling for them; it would be untested code guarding nothing.
 */
export function normalisePath(path) {
  let p = String(path || '').split('?')[0];
  // Decode BEFORE matching so the gate classifies the same string the upstream
  // will route on. Malformed input is left as-is; the gate treats it as hostile
  // via isDoubleEncodedPath() rather than guessing at its intent.
  if (p.includes('%')) {
    try { p = decodeURIComponent(p); } catch { /* keep raw — caller fails closed */ }
  }
  return p
    .replace(/\/{2,}/g, '/')
    .replace(/\/+$/, '')
    .toLowerCase() || '/';
}

/** True iff `path` (relative to /api/aria) is operator/infra surface. */
export function isInfraAriaPath(path) {
  const clean = normalisePath(path);
  return INFRA_ARIA_PATHS.some((p) => matchesPrefix(clean, p));
}

/**
 * Required role for an /api/aria request, or null when the path is customer
 * surface and must not be gated at all.
 *
 * Reads (GET/HEAD) → 'poweruser' (admin satisfies it, per roleSatisfies).
 * Mutations on infra → 'admin'. The read/write split is what makes `poweruser`
 * meaningfully view-only (R-F2773): a poweruser can watch every infra surface but
 * cannot trigger a seed run, a weekly adversarial sweep, or a self-deploy.
 */
export function requiredRoleForAriaPath(method, path) {
  if (!isInfraAriaPath(path)) return null;
  return MUTATING.includes(String(method || '').toUpperCase()) ? 'admin' : 'poweruser';
}
