export const meta = {
  name: 'verify-aria-web-gap-analysis',
  description: "Independently cross-check ARIA's aria-web (Node/Angular) gap analysis against the real code (§23)",
  phases: [
    { title: 'Verify findings', detail: 'one grounded code-read per claim → verdict + file:line' },
    { title: 'Cross-check synthesis', detail: 'group verdicts, surface where ARIA was wrong, list what needs a live probe' },
  ],
}

// ARIA's aria-web findings (her detailed table = 35 rows; her summary claims 37 — tally mismatch, note it).
// Node tier: server.mjs (repo root), lib/*.mjs, frontend/ (Angular), Dockerfile.web, fly.web.toml,
// .github/workflows/, services/wa-listener/.
const F = [
  { id: 1, layer: 'Testing', sev: 'CRIT', claim: 'No automated tests for the Node backend — server.mjs has zero test files, lib/ has no test dir; only 4 manual scripts in services/wa-listener.' },
  { id: 2, layer: 'Frontend', sev: 'CRIT', claim: 'No automated tests for the Angular frontend — tsconfig.spec.json + karma.conf.js exist but there are no .spec.ts files under frontend/src/app.' },
  { id: 3, layer: 'Backend', sev: 'CRIT', claim: 'No circuit breakers on Node→aria-intel HTTP calls (brain absorb, lead hunt, extract-document proxy). errorTracker.mjs has a CircuitBreaker class that is not wired around brain calls.' },
  { id: 4, layer: 'Backend', sev: 'CRIT', claim: 'No request timeout on the brain proxy (ariaProxy /api/aria/*); only /extract-document has a 120s timeout. A hung brain exhausts sockets.' },
  { id: 5, layer: 'Frontend', sev: 'CRIT', claim: 'Angular SPA is dead/unused — Dockerfile.web excludes frontend/dist (comment: deleted in R-F832); public/ serves static HTML instead.' },
  { id: 6, layer: 'Backend', sev: 'CRIT', claim: 'No circuit breaker on the brain bridge (runAndCacheBridgeVerdict runs at boot + every 60s); if the brain is down for hours every brain-absorb still fires and times out.' },
  { id: 7, layer: 'Telegram', sev: 'HIGH', claim: 'No rate limiting on the 20+ Telegram commands (/status,/sweep,/brief,/aria,/deal,/screen…) — a user spamming /aria could burn the LLM budget.' },
  { id: 8, layer: 'Telegram', sev: 'HIGH', claim: 'No input validation on the Telegram /aria command — handleAriaCommand passes user input directly to the LLM (no length limit, no injection guard).' },
  { id: 9, layer: 'Auth', sev: 'HIGH', claim: 'SSE /api/search/deep accepts the auth token via query param (?token=), leaking it to server logs and referrer headers.' },
  { id: 10, layer: 'Billing', sev: 'HIGH', claim: '/api/billing/webhook is mounted express.raw({type:"*/*"}) with no Stripe signature verification and no body schema validation.' },
  { id: 11, layer: 'Auth', sev: 'HIGH', claim: 'No session expiry — SESSION_TTL=None (Python) and the Node side has no expiry; a leaked JWT is valid forever.' },
  { id: 12, layer: 'Auth', sev: 'HIGH', claim: 'No CSRF protection for any cookie-authenticated routes (JWT-in-header paths are fine).' },
  { id: 13, layer: 'Deploy', sev: 'HIGH', claim: 'No dependency vulnerability scanning — package.json has 200+ deps; no npm audit in CI, no Dependabot, no Snyk.' },
  { id: 14, layer: 'Deploy', sev: 'HIGH', claim: 'No container image vuln scanning; Dockerfile.web installs python3/make/g++/git as build deps and does not remove them (no multi-stage build).' },
  { id: 15, layer: 'Observability', sev: 'MED', claim: 'No structured logging — server.mjs uses console.log/warn/error throughout; no levels, no JSON, no correlation IDs.' },
  { id: 16, layer: 'Observability', sev: 'MED', claim: 'No request-ID correlation across the Node→brain call chain.' },
  { id: 17, layer: 'Observability', sev: 'MED', claim: 'No /metrics Prometheus endpoint (request count/latency/error rate).' },
  { id: 18, layer: 'Backend', sev: 'MED', claim: 'No graceful shutdown / SIGTERM handler — Fly sends SIGTERM on deploy so in-flight requests are dropped (502s).' },
  { id: 19, layer: 'Backend', sev: 'MED', claim: 'No connection pooling for brain HTTP calls — every proxy call makes a new fetch() with no keep-alive http.Agent.' },
  { id: 20, layer: 'Backend', sev: 'MED', claim: 'No caching for /api/data (returns ~500KB sweep data every request; no ETag/Last-Modified).' },
  { id: 21, layer: 'Backend', sev: 'MED', claim: 'No cursor/offset pagination on /api/learning/outcomes (only a limit param).' },
  { id: 22, layer: 'Backend', sev: 'MED', claim: 'No input validation on /api/compliance/screen (sellerCountry/buyerCountry/productCategory not validated as ISO/known codes).' },
  { id: 23, layer: 'Auth', sev: 'MED', claim: 'No audit logging on admin actions (/api/admin/users/*) — no record of force-logout or permission changes.' },
  { id: 24, layer: 'Backend', sev: 'MED', claim: 'No abort on SSE /api/search/deep client disconnect — deep search keeps running server-side (wasted compute).' },
  { id: 25, layer: 'WhatsApp', sev: 'MED', claim: 'No WhatsApp message queue — ariaWhatsApp handles messages synchronously; a slow brain backs up the event loop.' },
  { id: 26, layer: 'Telegram', sev: 'MED', claim: 'No Telegram update_id deduplication — polling can deliver the same update multiple times.' },
  { id: 27, layer: 'Backend', sev: 'LOW', claim: 'No TypeScript in backend — server.mjs and lib/*.mjs are plain JS (no type checking).' },
  { id: 28, layer: 'Backend', sev: 'LOW', claim: 'No environment validation at startup — missing ARIA_API_TOKEN only surfaces on the first brain call.' },
  { id: 29, layer: 'Backend', sev: 'LOW', claim: 'No Redis health check at boot — redisAdapter.isConfigured checks REDIS_URL but never PINGs.' },
  { id: 30, layer: 'WhatsApp', sev: 'LOW', claim: 'No /api/health/wa endpoint on aria-web to probe aria-wa liveness.' },
  { id: 31, layer: 'Docs', sev: 'LOW', claim: 'No documentation for the 40+ lib modules (minimal/no JSDoc).' },
  { id: 32, layer: 'Deploy', sev: 'LOW', claim: 'No CI pipeline for aria-web — .github/workflows has deploy for aria-intel but not aria-web; web is deployed manually.' },
  { id: 33, layer: 'Deploy', sev: 'LOW', claim: 'No canary for aria-web — fly.web.toml uses rolling, not blue/green.' },
  { id: 34, layer: 'Backend', sev: 'LOW', claim: 'No database migrations — user store is a JSON file (users.json).' },
  { id: 35, layer: 'Backend', sev: 'LOW', claim: 'No backup verification / restore drills for Fly volumes.' },
]

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    id: { type: 'number' },
    verdict: { type: 'string', enum: ['confirmed', 'refuted', 'partial', 'unverifiable_from_code'] },
    evidence: { type: 'string', description: 'file:line citations proving the verdict — REQUIRED' },
    note: { type: 'string', description: 'what is actually true; if partial/refuted, exactly where ARIA was wrong' },
    needs_probe: { type: 'string', description: 'if unverifiable, the exact live probe needed (flyctl secrets, curl, etc.)' },
    real_severity: { type: 'string', enum: ['CRIT', 'HIGH', 'MED', 'LOW', 'NONE'] },
  },
  required: ['id', 'verdict', 'evidence', 'note', 'real_severity'],
}

phase('Verify findings')
const verdicts = await parallel(F.map(f => () =>
  agent(
    `You are INDEPENDENTLY cross-checking a claim ARIA made about the aria-web Node/Angular tier (repo root C:\\code\\crucix). Do NOT trust the claim — READ the actual code.\n\n` +
    `Relevant files: server.mjs (repo root, the Node monolith), lib/*.mjs, frontend/ (Angular), Dockerfile.web, fly.web.toml, .github/workflows/, services/wa-listener/ (aria_wa_listener.mjs + its test_*.mjs/.cjs), errorTracker.mjs.\n\n` +
    `FINDING #${f.id} [${f.layer}/${f.sev}]: ${f.claim}\n\n` +
    `Locate the relevant code (grep server.mjs and lib/). Classify:\n` +
    `- confirmed: code matches the claim (cite the file:line proving the gap — e.g. the route/handler with no timeout/breaker/validation).\n` +
    `- refuted: code contradicts the claim (e.g. there IS a SIGTERM handler / Stripe signature check / timeout — cite it).\n` +
    `- partial: partly true — say exactly which half holds (e.g. SOME brain calls have timeouts, others don't).\n` +
    `- unverifiable_from_code: depends on a live runtime/secret/external state — say what probe is needed.\n\n` +
    `Every verdict REQUIRES a file:line citation. If you cannot find the named file/symbol, that may refute the claim — say so. Be precise and skeptical (§23 cross-check). Note: a deliberately-documented state (e.g. "dead Angular SPA, excluded by design") is NOT a code defect — mark such as partial/refuted with the by-design note.`,
    { label: `verify:#${f.id}-${f.layer}`, phase: 'Verify findings', agentType: 'Explore', schema: VERDICT_SCHEMA, effort: 'high' }
  )
))
const got = verdicts.filter(Boolean)
const byV = v => got.filter(x => x.verdict === v)
log(`Verified ${got.length}/${F.length}: confirmed=${byV('confirmed').length} refuted=${byV('refuted').length} partial=${byV('partial').length} unverifiable=${byV('unverifiable_from_code').length}`)

phase('Cross-check synthesis')
const merged = got.map(v => {
  const orig = F.find(f => f.id === v.id) || {}
  return { id: v.id, layer: orig.layer, aria_sev: orig.sev, claim: orig.claim, verdict: v.verdict, real_severity: v.real_severity, evidence: v.evidence, note: v.note, needs_probe: v.needs_probe }
})

const report = await agent(
  `Write the §23 CROSS-CHECK REPORT on ARIA's aria-web (Node/Angular) gap analysis. You independently re-verified every claim against the code; results below.\n\n` +
  `VERIFIED RESULTS (${merged.length}):\n${JSON.stringify(merged, null, 1)}\n\n` +
  `Produce a decisive markdown report:\n` +
  `1. Headline: how many claims hold vs are wrong/overstated; overall accuracy. Note the tally mismatch (her detailed table = 35 rows but her summary claims 37, with per-severity counts that do not reconcile).\n` +
  `2. CONFIRMED real gaps — table by REVIEWER severity with file:line, the ones genuinely worth fixing for an enterprise product. Call out which are security-critical (auth/billing/CSRF/token-leak).\n` +
  `3. REFUTED / OVERSTATED — table where ARIA was wrong, cite the contradicting file:line. Include by-design states she flagged as gaps (e.g. the dead Angular SPA).\n` +
  `4. UNVERIFIABLE-FROM-CODE — claims needing a live probe; give the exact commands.\n` +
  `5. Re-prioritized top fixes (value × effort) for aria-web, distinct from ARIA's top-5 if evidence warrants. Security gaps first.\n` +
  `Be honest where ARIA was right AND wrong. Concrete, citation-backed.`,
  { label: 'cross-check-report', phase: 'Cross-check synthesis', effort: 'high' }
)

return { verified: got.length, confirmed: byV('confirmed').length, refuted: byV('refuted').length, partial: byV('partial').length, unverifiable: byV('unverifiable_from_code').length, report }
