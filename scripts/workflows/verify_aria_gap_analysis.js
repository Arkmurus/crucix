export const meta = {
  name: 'verify-aria-gap-analysis',
  description: "Independently cross-check ARIA's 40-row gap analysis against the real code (§23) — confirm/refute/partial per finding with file:line",
  phases: [
    { title: 'Verify findings', detail: 'one grounded code-read per claim → verdict + evidence' },
    { title: 'Cross-check synthesis', detail: 'group verdicts, surface where ARIA was wrong, list what needs a live/secrets probe' },
  ],
}

// ARIA's 40 findings, verbatim claims. Each is independently re-checked against code.
const F = [
  { id: 1, layer: 'LLM', sev: 'CRIT', claim: 'No local/sovereign LLM in prod: ARIA_LLM_URL unset (SFT adapter trained R-F837 but not wired), OLLAMA_URL unset; every LLM call goes to DeepSeek.' },
  { id: 2, layer: 'LLM', sev: 'CRIT', claim: 'LLMResponseCache exists in resilience.py but is NOT wired into the provider chain (factory/MeteredProvider).' },
  { id: 3, layer: 'LLM', sev: 'CRIT', claim: 'LLMRequestQueue exists but is NOT wired around the provider chain (no max-concurrency gate).' },
  { id: 4, layer: 'LLM', sev: 'CRIT', claim: 'LLMHealthChecker exists but is NOT started in main.py lifespan().' },
  { id: 5, layer: 'Search', sev: 'CRIT', claim: 'archive.is circuit breaker is permanently OPEN (9/9 failures, 1h cooldown) and will never self-recover.' },
  { id: 6, layer: 'Search', sev: 'CRIT', claim: 'openalex breaker flaps OPEN→HALF_OPEN→OPEN (13/19 failures, 10-min cooldown).' },
  { id: 7, layer: 'Search', sev: 'CRIT', claim: 'google_news and bing_news backends have NO circuit breakers — silent [] on failure.' },
  { id: 8, layer: 'Search', sev: 'CRIT', claim: 'researcher.py web_search()/DDG call bypasses the circuit breaker (only web_search.py:_search_duckduckgo has one).' },
  { id: 9, layer: 'Search', sev: 'CRIT', claim: 'searxng self-host path (_search_searxng ~lines 264-284) has NO circuit breaker; only the dead public-instances loop has one.' },
  { id: 10, layer: 'Search', sev: 'CRIT', claim: 'crawl_enhancements.fetch_via_wayback() page-fetch fallback has NO circuit breaker.' },
  { id: 11, layer: 'LLM', sev: 'HIGH', claim: 'tier_router.py defines 5 tiers (intent→provider) but fallback.py provider chain does NOT use tier_router.select_provider().' },
  { id: 12, layer: 'LLM', sev: 'HIGH', claim: 'local_llm.py exists and factory supports "ollama" but OLLAMA_URL unset — low-stakes calls all go to paid DeepSeek.' },
  { id: 13, layer: 'LLM', sev: 'HIGH', claim: 'prompt_budget.py exists but is NOT wired into OpenAICompatProvider.complete() — long prompts can 413.' },
  { id: 14, layer: 'Coding', sev: 'HIGH', claim: 'ARIACoder is DORMANT — ARIA_CODER_ENABLED not set; the self-coding pipeline never fires.' },
  { id: 15, layer: 'Coding', sev: 'HIGH', claim: 'No Claude review hook — ARIA_CODER_CLAUDE_REVIEW_ENABLED forward-looking; self-coded changes stage without 2nd-opinion.' },
  { id: 16, layer: 'Infra', sev: 'HIGH', claim: 'Two separate CircuitBreaker classes exist (intel/circuit_breaker.py and intel/self_healing.py) with different fields.' },
  { id: 17, layer: 'Memory', sev: 'HIGH', claim: 'rag_store.py has a _cold_collection placeholder (R-F238) that is never populated — no cold-storage offload.' },
  { id: 18, layer: 'Memory', sev: 'HIGH', claim: 'memory_router.py / ARIAMemoryRouter exists but is NOT wired into aria_engine.py chat context assembly.' },
  { id: 19, layer: 'Reasoning', sev: 'HIGH', claim: 'self_sufficient.py:SymbolicReasoner exists but is NOT wired — deterministic tasks still hit the LLM.' },
  { id: 20, layer: 'Reasoning', sev: 'HIGH', claim: 'self_sufficient.py:KnowledgeAugmentedResponder exists but is NOT wired as a pre-LLM RAG short-circuit.' },
  { id: 21, layer: 'Testing', sev: 'MED', claim: '6985 tests collected; full run times out (>600s); current pass/fail rate unknown.' },
  { id: 22, layer: 'Testing', sev: 'MED', claim: 'No test parallelization (pytest-xdist not configured).' },
  { id: 23, layer: 'Testing', sev: 'MED', claim: 'No integration tests for the LLM chain — all LLM tests mock the provider; fallback/cooldown/switching untested e2e.' },
  { id: 24, layer: 'Testing', sev: 'MED', claim: 'No performance regression tests (no p95/p99 latency benchmarks for critical paths).' },
  { id: 25, layer: 'Observability', sev: 'MED', claim: 'brain_hook latency breaker state is only on /brain/stats, NOT merged into the /circuit-breakers endpoint.' },
  { id: 26, layer: 'Observability', sev: 'MED', claim: 'MeteredProvider tracks total cost but not per-backend or per-intent — no per-backend cost dashboard.' },
  { id: 27, layer: 'Infra', sev: 'MED', claim: 'self_healing.py SelfHealingOrchestrator is NOT started in main.py lifespan().' },
  { id: 28, layer: 'Infra', sev: 'MED', claim: 'memory_leak_detector.py exists but is not started.' },
  { id: 29, layer: 'Infra', sev: 'MED', claim: 'deadlock_detector is Optional[Any]=None in the orchestrator — never instantiated.' },
  { id: 30, layer: 'Infra', sev: 'MED', claim: 'WriteAheadLog.replay() is called by the orchestrator but the orchestrator is not started — crash recovery best-effort.' },
  { id: 31, layer: 'LLM', sev: 'LOW', claim: 'gemini.py exists, factory supports it, but GEMINI_API_KEY unset.' },
  { id: 32, layer: 'LLM', sev: 'LOW', claim: 'factory supports Groq but GROQ_API_KEY unset.' },
  { id: 33, layer: 'LLM', sev: 'LOW', claim: 'factory supports OpenRouter but OPENROUTER_API_KEY unset.' },
  { id: 34, layer: 'LLM', sev: 'LOW', claim: 'factory supports Mistral but MISTRAL_API_KEY unset.' },
  { id: 35, layer: 'LLM', sev: 'LOW', claim: 'factory supports MiniMax but MINIMAX_API_KEY unset.' },
  { id: 36, layer: 'Search', sev: 'LOW', claim: 'opensanctions.org breaker never exercised (0/0 calls) — entity screening path may not be triggering.' },
  { id: 37, layer: 'Testing', sev: 'LOW', claim: 'No per-feed RSS breaker isolation test (only one feed tested).' },
  { id: 38, layer: 'Infra', sev: 'LOW', claim: 'intel/circuit_breaker.py is in-process only — no Redis persistence; deploy resets all breaker states.' },
  { id: 39, layer: 'Infra', sev: 'LOW', claim: 'circuit_breaker.py:get_breaker() fires wire_success() on every access, not only on state transitions (21,885 signals).' },
  { id: 40, layer: 'Safety', sev: 'LOW', claim: 'pre-commit hook checks brain wiring but does NOT check that new HTTP backends have circuit breakers.' },
]

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    id: { type: 'number' },
    verdict: { type: 'string', enum: ['confirmed', 'refuted', 'partial', 'unverifiable_from_code'] },
    evidence: { type: 'string', description: 'file:line citations proving the verdict — REQUIRED, no claim without a cite' },
    note: { type: 'string', description: 'what is actually true; if partial/refuted, exactly where ARIA was wrong' },
    needs_probe: { type: 'string', description: 'if unverifiable from code, the exact live/secrets probe needed (e.g. flyctl secrets list, /circuit-breakers curl)' },
    real_severity: { type: 'string', enum: ['CRIT', 'HIGH', 'MED', 'LOW', 'NONE'] },
  },
  required: ['id', 'verdict', 'evidence', 'note', 'real_severity'],
}

phase('Verify findings')
const verdicts = await parallel(F.map(f => () =>
  agent(
    `You are INDEPENDENTLY cross-checking a claim ARIA made about the ARIA codebase (repo root C:\\code\\crucix, Python under aria_service/). Do NOT trust the claim — READ the actual code and decide.\n\n` +
    `FINDING #${f.id} [${f.layer}/${f.sev}]: ${f.claim}\n\n` +
    `Verify against the real code. Locate the file(s), the class/function named, and whether it is actually wired (imported AND called) on the production path (main.py lifespan / aria_engine / the provider factory+fallback chain / the search backends). Classify:\n` +
    `- confirmed: code matches the claim (cite the file:line that proves the gap, e.g. the class exists but no call site wires it).\n` +
    `- refuted: code contradicts the claim (e.g. it IS wired — cite the call site).\n` +
    `- partial: partly true — say exactly which half holds.\n` +
    `- unverifiable_from_code: the claim depends on an env-var VALUE on Fly or a LIVE runtime stat (breaker hit counts like "9/9 failures", "0/0 calls") that is NOT in the code — say what probe is needed (flyctl secrets, /circuit-breakers curl). Still confirm whether the underlying CODE (the breaker/class/env-read) exists.\n\n` +
    `Every verdict REQUIRES a file:line citation. If you cannot find the named file/class, that itself may refute the claim — say so. Be precise and skeptical; this is a §23 cross-check, not a rubber stamp.`,
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
  `Write the §23 CROSS-CHECK REPORT on ARIA's 40-row gap analysis. You independently re-verified every claim against the code; results below.\n\n` +
  `VERIFIED RESULTS (${merged.length}):\n${JSON.stringify(merged, null, 1)}\n\n` +
  `Produce a decisive markdown report:\n` +
  `1. Headline verdict: how many of ARIA's claims hold up, how many are wrong/overstated, how accurate was she overall (she said 39 total but listed 40 rows — note that).\n` +
  `2. CONFIRMED real gaps — table by severity, with file:line, the ones genuinely worth fixing for an enterprise product.\n` +
  `3. REFUTED / OVERSTATED claims — table showing exactly where ARIA was wrong (cite the contradicting file:line) — this is the most important section.\n` +
  `4. UNVERIFIABLE-FROM-CODE — the claims that need a live probe (flyctl secrets / /circuit-breakers curl); list the exact commands so the operator can settle them.\n` +
  `5. Re-prioritized top fixes (what to actually do, value x effort), distinct from ARIA's own top-5 if the evidence warrants.\n` +
  `Be honest where ARIA was right AND where she was wrong. Concrete, citation-backed.`,
  { label: 'cross-check-report', phase: 'Cross-check synthesis', effort: 'high' }
)

return { verified: got.length, confirmed: byV('confirmed').length, refuted: byV('refuted').length, partial: byV('partial').length, unverifiable: byV('unverifiable_from_code').length, report }
