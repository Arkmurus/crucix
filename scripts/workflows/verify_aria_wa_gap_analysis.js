export const meta = {
  name: 'verify-aria-wa-gap-analysis',
  description: "Independently cross-check ARIA's aria-wa (WhatsApp listener) gap analysis against the real code (§23)",
  phases: [
    { title: 'Verify findings', detail: 'one grounded code-read per claim → verdict + file:line' },
    { title: 'Cross-check synthesis', detail: 'group verdicts; prioritize actionable CRIT/security fixes' },
  ],
}

// aria-wa = services/wa-listener/aria_wa_listener.mjs (~2330 lines) + its
// Dockerfile + package.json. Shared infra: lib/observability/errorTracker.mjs.
// ARIA already self-corrected 3 claims (timeout, async-job eviction, Redis
// persistence) — verify those stay corrected too.
const F = [
  { id: 1, layer: 'Resilience', sev: 'CRIT', claim: 'No circuit breaker on brainFetch — if the brain is down, every message retries 3x then throws; errorTracker CircuitBreaker exists but is not imported here.' },
  { id: 2, layer: 'Lifecycle', sev: 'CRIT', claim: 'No graceful shutdown — no SIGTERM handler; Fly SIGTERM on deploy drops in-flight message processing.' },
  { id: 3, layer: 'Resilience', sev: 'CRIT', claim: 'No circuit breaker on the brain health bridge (brainFetchHealth) — brain down for hours still probed every message.' },
  { id: 4, layer: 'Telegram/WA', sev: 'CRIT', claim: 'No rate limiting on compliance commands (/screen, /classify, /sanctions, /risk in handleCommand) — a user can spam and burn LLM budget.' },
  { id: 5, layer: 'Observability', sev: 'HIGH', claim: 'No structured logging — console.log/warn/error throughout; no levels/JSON/correlation IDs.' },
  { id: 6, layer: 'Observability', sev: 'HIGH', claim: 'No request-ID propagated WA→brain (requestId exists for delivery outcomes but not in brain logs).' },
  { id: 7, layer: 'Concurrency', sev: 'HIGH', claim: 'No message queue — messages processed synchronously in the messages.upsert handler; a slow brain blocks the event loop.' },
  { id: 8, layer: 'Validation', sev: 'HIGH', claim: 'No input validation on /api/wa-listener/send (group_id/to/chat_id/jid aliases) — no JID-format validation.' },
  { id: 9, layer: 'Deploy', sev: 'HIGH', claim: 'Single-stage Dockerfile installs python3/make/g++ build deps and does not remove them in the final image.' },
  { id: 10, layer: 'Deploy', sev: 'HIGH', claim: 'No CI pipeline for aria-wa — no GitHub Actions workflow; deployed manually.' },
  { id: 11, layer: 'Observability', sev: 'MED', claim: 'No Prometheus /metrics endpoint (/health shows basic state only).' },
  { id: 12, layer: 'Perf', sev: 'MED', claim: 'No connection pooling for brain HTTP — every brainFetch makes a new connection.' },
  { id: 13, layer: 'Perf', sev: 'MED', claim: 'No caching for /groups — sock.groupFetchAllParticipating() called on every request.' },
  { id: 14, layer: 'API', sev: 'MED', claim: 'No cursor pagination on /messages (n param max 100 only).' },
  { id: 15, layer: 'Audit', sev: 'MED', claim: 'No audit logging on /reset-auth (destructive, no trail).' },
  { id: 16, layer: 'WA', sev: 'MED', claim: 'No periodic WhatsApp presence heartbeat (typing indicator every 10s during polls, but no presence heartbeat).' },
  { id: 17, layer: 'Testing', sev: 'MED', claim: 'No test for the Express routes — 4 test files cover resilience/formatter/source/requestId, none test the HTTP API endpoints.' },
  { id: 18, layer: 'Backend', sev: 'LOW', claim: 'No TypeScript.' },
  { id: 19, layer: 'Backend', sev: 'LOW', claim: 'No environment validation at startup.' },
  { id: 20, layer: 'Backend', sev: 'LOW', claim: 'No Redis health check at startup.' },
  { id: 21, layer: 'Docs', sev: 'LOW', claim: 'No OpenAPI/Swagger documentation.' },
  { id: 22, layer: 'Deploy', sev: 'LOW', claim: 'No canary deployment.' },
  { id: 23, layer: 'Backend', sev: 'LOW', claim: 'No database migrations.' },
  { id: 24, layer: 'Backend', sev: 'LOW', claim: 'No backup verification.' },
]

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    id: { type: 'number' },
    verdict: { type: 'string', enum: ['confirmed', 'refuted', 'partial', 'unverifiable_from_code'] },
    evidence: { type: 'string', description: 'file:line citations — REQUIRED' },
    note: { type: 'string' },
    real_severity: { type: 'string', enum: ['CRIT', 'HIGH', 'MED', 'LOW', 'NONE'] },
    fix_mirrors: { type: 'string', description: 'if this mirrors an already-shipped aria-web fix (R-F1796 brainAbsorb breaker / R-F1797 SIGTERM / R-F1798 rate-limit / R-F1799 multi-stage Docker / R-F1794 CI), name it' },
  },
  required: ['id', 'verdict', 'evidence', 'note', 'real_severity'],
}

phase('Verify findings')
const verdicts = await parallel(F.map(f => () =>
  agent(
    `Independently cross-check a claim ARIA made about aria-wa (the WhatsApp listener). Repo root C:\\code\\crucix. Main file: services/wa-listener/aria_wa_listener.mjs (~2330 lines); also its Dockerfile (services/wa-listener/Dockerfile), package.json, and shared lib/observability/errorTracker.mjs. READ the actual code — do not trust the claim.\n\n` +
    `FINDING #${f.id} [${f.layer}/${f.sev}]: ${f.claim}\n\n` +
    `Classify confirmed / refuted / partial / unverifiable_from_code with a file:line citation. Note: aria-wa just had its sibling aria-web hardened — several of these mirror shipped fixes (brainAbsorb circuit breaker R-F1796, graceful SIGTERM R-F1797, command rate-limit R-F1798, multi-stage Dockerfile R-F1799, CI npm-audit R-F1794). If this finding mirrors one of those, set fix_mirrors. Be precise and skeptical (§23).`,
    { label: `verify:#${f.id}-${f.layer}`, phase: 'Verify findings', agentType: 'Explore', schema: VERDICT_SCHEMA, effort: 'high' }
  )
))
const got = verdicts.filter(Boolean)
const byV = v => got.filter(x => x.verdict === v)
log(`Verified ${got.length}/${F.length}: confirmed=${byV('confirmed').length} refuted=${byV('refuted').length} partial=${byV('partial').length} unverifiable=${byV('unverifiable_from_code').length}`)

phase('Cross-check synthesis')
const merged = got.map(v => {
  const o = F.find(f => f.id === v.id) || {}
  return { id: v.id, layer: o.layer, aria_sev: o.sev, claim: o.claim, verdict: v.verdict, real_severity: v.real_severity, evidence: v.evidence, note: v.note, fix_mirrors: v.fix_mirrors }
})
const report = await agent(
  `Write the §23 CROSS-CHECK REPORT on ARIA's aria-wa gap analysis. You re-verified all 24 claims; results below.\n\n` +
  `VERIFIED (${merged.length}):\n${JSON.stringify(merged, null, 1)}\n\n` +
  `Produce markdown:\n` +
  `1. Headline accuracy (right vs wrong vs overstated). ARIA self-corrected 3 claims pre-report — note if that improved her accuracy vs the earlier aria-web/Python reports.\n` +
  `2. CONFIRMED real gaps by reviewer severity with file:line; mark 🔒 security and tag which mirror an already-shipped aria-web fix (so we can port the proven pattern).\n` +
  `3. REFUTED/OVERSTATED with contradicting file:line.\n` +
  `4. A STAGE-NOW list: the CRIT/security fixes worth implementing immediately, in priority order, noting the aria-web R-number whose pattern to port.\n` +
  `Concrete, citation-backed.`,
  { label: 'cross-check-report', phase: 'Cross-check synthesis', effort: 'high' }
)
return { verified: got.length, confirmed: byV('confirmed').length, refuted: byV('refuted').length, partial: byV('partial').length, report }
