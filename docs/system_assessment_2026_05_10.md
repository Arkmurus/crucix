# ARIA System Assessment — 2026-05-10 (360° Overview)

**Last commit at writing**: `f8339de` (R-F135 auto-language English country names)
**Sprint window**: 2026-05-09 → 2026-05-10 (R-F37 → R-F136)
**Composite autonomy**: 71% HIGH (4 signals, 2 awaiting first chat-with-tool-use)
**System health**: AMBER (33 modules / 31 PASS / 2 graceful-degrade WARN / 0 FAIL)
**Cost MTD**: $12.39 / $300 cap (4.1% utilisation, projected $41.74/month)

---

## 1 — What was shipped today (2026-05-10)

| R-F | Layer | What | fly | seenode |
|---|---|---|---|---|
| F121 | DD | Layer 8 — counter-intelligence wired into orchestrator | ✓ |  |
| F122 | DD | Layer 9 — sanctions divergence wired | ✓ |  |
| F123 | DD | Layer 10 — forensic (Benford + TBML) wired, conservative gate | ✓ |  |
| F124 | Search | Memory-first inversion — corpus before web | ✓ |  |
| F125 | Search | Auto-language fan-out — 9 languages by Unicode + native marker | ✓ |  |
| F126 | Search | Defence-show calendar (18 events) + Wayback widening on 4xx/5xx | ✓ |  |
| F127 | UI | aria-brain.html overflow guard — explorer.html fix pattern applied globally |  | ✓ |
| F128 | Coverage | Heatmap synonym matcher — was 100% absent, now token-level + 13 synonym sets | ✓ |  |
| F129 | UI | Explorer snippet HTML strip — Google News `<a href=` no longer leaks |  | ✓ |
| F130 | UI | DD library Severity column populates from `risk` fallback | ✓ | ✓ |
| F131 | Cost | Real provider attribution — anthropic/deepseek/groq instead of `fallback` | ✓ |  |
| F132 | UI | LLM cost panel redesign — utilisation bar + side-by-side breakdowns + collapsible top calls |  | ✓ |
| F133 | DD | Sanctions divergence token-overlap filter — drops unrelated fuzzy matches | ✓ | ✓ |
| F134 | Infra | `intel_ledger.get_recent` / `all_signals` / `recent_signals` helpers added | ✓ |  |
| F135 | Search | Auto-language detects English country names (Turkey / Brazil / UAE / Korea / etc) | ✓ |  |
| F136 | Awareness | 9 new modules registered in self_diagnostic catalogue | ✓ |  |

**16 R-numbered changes today, 8 commits.** Cumulative since 2026-04-26: ~141 R-numbers.

---

## 2 — DD pipeline: 7 → 10 layers

The orchestrator now runs 10 sequential layers, all with timeouts and fail-open exception handlers. Every new layer attaches its result as an instance attribute on the report (no schema break for old reports).

| # | Layer | Timeout | Fail-open | What it answers |
|---|---|---|---|---|
| 1 | Identity | 60s | yes | Who is this entity? Registered? Sanctioned? |
| 2 | Network | 30s | yes | Officer/director/UBO graph + RCA edges |
| 3 | Compliance | 30s | yes | Export control, EUC, ECCN/Wassenaar/ITAR |
| 4 | Digital | 60s | yes | Web footprint + deep_research + ghost score |
| 5 | Commercial coherence (Layer 5c) | 10s | yes | Is the deal structure coherent? Payment norms / licence chain |
| 5b | Deception scoring (Clause 16) | inline | yes | Linguistic deception indicators on counterparty text |
| 6 | Verification | 30s | yes | Confidence floor + grounded rate + conflict count |
| 7 | Synthesis | 10s | yes | ACH matrix + risk classification + SAR trigger |
| **8** | **Counter-intelligence (R-F121)** | **8s** | **yes** | **Narrative-shift / coordinated press / tier contradiction** |
| **9** | **Sanctions divergence (R-F122)** | **10s** | **yes** | **Cross-list jurisdictional gap (OFAC vs OFSI vs UN)** |
| **10** | **Forensic — Benford + TBML (R-F123)** | **inline** | **yes** | **Fabricated-figure flag (≥50 vals) + transaction anomaly classifier** |

**Render**: `dd_schema.render_markdown` prints all three new layers between commercial-coherence and synthesis with conditional gating (only renders when populated).

**Cost attribution**: per-layer `meta.cost_usd` is rarely populated (LLM cost is recorded out-of-band by MeteredProvider). R-F119 backfills `total_cost_usd` from `cost_tracker.list_recent_calls()` for the run window so DD reports show real spend.

---

## 3 — Search backbone: 6 → 8+ backends + memory-first

The main `search()` parallel-gather now runs:

| # | Backend | Cost | Gating | Notes |
|---|---|---|---|---|
| 0 | **Memory (R-F124)** | $0 | always | rag_store.search() — gets +0.5 relevance bonus |
| 1 | Brave Search API | paid | `BRAVE_SEARCH_API_KEY` | OPEN today; circuit breaker on auth fail |
| 2 | SearXNG | $0 | requires self-host | Phase 1 of independence roadmap; not deployed |
| 3 | DuckDuckGo HTML scrape | $0 | always | R-F120 fallback |
| 4 | Google News RSS | $0 | always | Always reliable |
| 5 | Bing News RSS | $0 | always | R-F120 fallback |
| 6 | Academic (OpenAlex / Semantic Scholar / CrossRef) | $0 | always | Three sub-backends |
| 7 | **Defence-event calendar (R-F126)** | varies | match-gated | 18 events: SAHA/IDEX/Eurosatory/DSEI/AUSA/etc |
| +N | **Auto-language fan-out (R-F125, R-F135)** | varies | detect-gated | adds Brave + Google News in detected lang, capped at 3 extras |

**11-language coverage** for auto-language: `tr ko ja zh hi ru ar pt es fr de` (plus the Unicode-script auto-detection for non-Latin queries).

**Wayback fallback** widened on `extract_url_text` and `read_article` to cover 401/402/403/404/410/451/500/502/503/504. Post-event press URLs commonly 404 once CMS rotates content.

---

## 4 — Brain awareness map

ARIA has 5 substrates that together constitute her "memory":

| Substrate | Volume today | What it stores |
|---|---|---|
| Knowledge | 20,603 facts | Topic-keyed extracted facts (deduplicated, replicated to disk) |
| Intel ledger | 19,589 signals | Time-series raw signals from sweeps + DDs + chats |
| RAG store | 76,631 chunks (17,600 docs + 59,031 facts) | Chromadb-indexed for semantic retrieval |
| Neural memory | 10,714 neurons / 9,279 edge groups | Hebbian co-activation between concepts |
| mem0 | 195 facts | Personal-notebook scoped to operator session |

### How a new capability becomes "known" to the brain

1. **File creation** — capability_manifest auto-derives every `.py` in `aria_service/intel/` (no hand-registration needed).
2. **Module registration** — for brain-feeding modules, must appear in `brain_hook._MODULE_TOPICS`.
3. **Self-diagnostic** — for production-critical modules, must appear in `self_diagnostic._MODULES` (R-F136 added 9 today).
4. **Endpoint** — for operator-callable, must have a route handler in `routes/aria.py` and (if cross-server) a proxy in `server.mjs` (R-F110/F117 catch-all middleware handles this for any new `/api/aria/*` route).
5. **Autonomous** — for time-driven modules, must have a task in `autonomous/tasks.yaml` with `enabled: true`.
6. **Constitution** — for behaviour-changing modules, must have a clause in `aria_engine.py:CONSTITUTION` (currently 23 clauses).

### After today's work: registration completeness

| Module | File | brain_hook | self_diagnostic | endpoint | autonomous_task | constitution-cited |
|---|---|---|---|---|---|---|
| counter_intelligence | ✓ | ✓ | ✓ (R-F136) | ✓ /security/counter-intel/scan | ✓ WEEKLY-COUNTER-INTEL | clause 16 |
| sanctions_divergence | ✓ | ✓ | ✓ (R-F136) | ✓ /sanctions/divergence | — | clauses 7+8 |
| forensic_benford | ✓ | ✓ | ✓ (R-F136) | ✓ /forensic/benford | — | clause 11 |
| tbml_detection | ✓ | ✓ | ✓ (R-F136) | ✓ /tbml/classify | — | clause 11 |
| coverage_heatmap | ✓ | — | ✓ (R-F136) | ✓ /learning/coverage | ✓ COVERAGE-HEATMAP-REFRESH | — |
| learning_progress | ✓ | — | ✓ (R-F136) | ✓ /learning/freshness | (R-F96 inline) | — |
| web_search | ✓ | ✓ | ✓ (R-F136) | (internal) | — | clauses 9+12 |
| intel_ledger | ✓ | ✓ | ✓ (R-F136) | (internal) | — | clauses 7+11 |
| dd_orchestrator | ✓ | ✓ | ✓ (R-F136) | ✓ /dd/* | (chat-triggered) | clauses 7+8+11+16+20 |

All 9 critical modules now self-diagnose every 15 min via `SELF-DIAGNOSTIC-15MIN`.

### Capability awareness gaps still open

- **Function-level introspection**: capability_manifest enumerates module names but not the new helper functions (`_query_memory`, `_detect_query_languages`, `_search_defence_event`, `_shares_substantive_token`, `JURISDICTION_SYNONYMS`). Brain knows the module exists but can't introspect "did the helper fire on the last query?"
  Fix: extend capability_manifest with public-function enumeration via `inspect.getmembers(mod, callable)`. ~1 hour.
- **R-F change log accessible to brain**: today's 16 R-numbered improvements aren't in any brain-readable index. Operator paste reveals them; ARIA herself wouldn't know what changed yesterday vs last week.
  Fix: scheduled `SPRINT-DIGEST-DAILY` task that runs `sprint_metrics.py --json --since=$(date -d 'yesterday')` and absorbs the result via brain_hook. ~2 hours.

---

## 5 — Self-diagnosis surface

### What ARIA can already self-diagnose

| Question | Endpoint / module | Frequency |
|---|---|---|
| Are my modules importable + entry-points present? | `/diagnostic/details` | 15 min |
| Is brain absorbing facts? | `learning/stats` | continuous |
| Is the autonomous engine firing? | `/autonomous/status` | continuous |
| Am I overconfident? | `/calibration/review` | nightly |
| Am I being attacked? | `/adversarial/stats` | weekly |
| What's my mastery per topic / region? | `/student/mastery/heatmap` | continuous |
| What's broken in my source seed? | `/sources/uptime` + sources.html | daily |
| What's my LLM spend trajectory? | `/cost/monthly` | continuous |
| What facts contradicted me? | `/verification/stats` | per-DD |
| Where are my knowledge gaps? | `/learning/coverage/gaps` | per-dashboard-load |
| Are operating-mode triggers firing? | `/operating-mode` | continuous |
| What did I learn in the last 24h? | `/learning/freshness` | continuous |

### What ARIA can NOT yet self-diagnose

- **"What changed in my codebase yesterday?"** — sprint metrics is operator-side only.
- **"Which DD layer is contributing most signal vs noise?"** — no per-layer effectiveness metric. Layer 8/9/10 fire but their contribution to the final risk classification isn't tracked.
- **"Which search backend is lifting my answer quality the most?"** — no per-backend signal-to-noise tracking. We know which backend returned a result; not whether the result mattered.
- **"Is auto-language fan-out actually finding things English-only didn't?"** — no A/B tracking.
- **"Has my response style drifted from what the operator approves?"** — style_learner exists but doesn't compare current vs baseline.

---

## 6 — Top recommendations to boost capability (next 30 days)

### P0 — operator-pending (no code can solve these)

1. **Top up Brave Search API** — circuit OPEN since 2026-05-09 morning. Solving this lifts general-web search quality 30-50% over DDG-only fallback.
2. **Rotate `ARIA_INTERNAL_TOKEN`** — was pasted in chat 2026-05-09; security hygiene.
3. **Investigate 3 quarantined DDs**: `dd_30477701e537`, `dd_adc7c7f87e4a`, third TBD via `/dd/quarantine`.
4. **Reject the 10 stale amendments** (oldest 2026-04-19) so the queue isn't a constant operator distraction.
5. **Set `WORLDBANK_SUBSCRIPTION_KEY` + `ACLED_EMAIL`** — closes the 2 graceful-degrade WARN entries on /diagnostic.
6. **Set `REPORT_SIGNING_KEY`** — removes UNSIGNED warning on R-F43 audit-grade PDFs.
7. **Set `ARIA_MIRROR_GROUPS` + `ARIA_COUNTERPARTY_CONTACTS`** — un-gates WA counterparty mirror.
8. **Deploy SearXNG container** + set `SEARXNG_URL` — brings the "sovereign general-web search" path online (Phase 1 of Independence Roadmap).

### P1 — code-shippable next (high leverage, low risk)

1. **Per-layer DD effectiveness scorer** — track which layer contributed which finding to the final risk classification; surface as "Layer 8 contributed 23% of all RED classifications this month". ~6 hours.
2. **Search-backend signal scorer** — when a search returns results that get cited in a chat answer, attribute the citation back to the backend. Reveals whether memory-first is paying off or if Brave is still doing the heavy lifting. ~4 hours.
3. **Function-level capability manifest (R-F137 candidate)** — enumerate public functions so brain knows about helpers like `_detect_query_languages`. ~1 hour.
4. **SPRINT-DIGEST-DAILY autonomous task (R-F138 candidate)** — runs sprint_metrics + absorbs to brain so ARIA knows what changed. ~2 hours.
5. **DD per-layer cost attribution** — wrap each layer in `cost_tracker.feature("dd_layer_<n>")` so the cost panel shows where the spend actually went, instead of attributing it to dd_orchestrator overall. ~2 hours.
6. **Live SAHA / IDEX / etc deep-link tester** — verify R-F126 defence-event auto-routing actually returns Turkish/Arabic content on the next operator query; fail loudly if it returns 0 results when the corpus has the data. ~3 hours.
7. **Reasoning library auto-curation** — the lightweight self-improve loop already exists; extend it to scan today's chat audit entries for "Sources: 0" patterns where there should have been retrieval, surface as gap, and seed RAG from the failed query. ~4 hours.

### P2 — strategic, multi-day

1. **ARIA-LLM v0.1 fine-tune** — once corpus accumulates 5K-10K example pairs (currently 280), rent GPU + run LoRA fine-tune. Phases 1-4 of the roadmap are coded; phase 5 (the actual training run) is operator-rentable for ~$200.
2. **Real-time DD progress streaming** — operators wait 60-150s for a DD to complete with no progress visibility. SSE endpoint that emits "Layer 1: Identity ✓ (12s) ... Layer 2: Network ⏳" would dramatically improve UX. ~1 day.
3. **Per-customer DD watermark + leak-trace** — code exists; never tested. If we want to sell DDs externally, this is required.
4. **OpenCorporates global registry direct API** — Phase 2 of the substrate-deepening plan. Today only 13 jurisdictions have direct registry adapters; OpenCorporates covers 130+.
5. **Telegram channel mirror as search input** — high-signal early intel, tier-D classified. Code framework exists; needs operator-curated channel list.
6. **LinkedIn entity-page auth** — currently flagged as "NOT checked" in DD output. Operator action gates this.
7. **Janes / Defence News / Shephard direct backends** — defence trade press is where contract data appears first. Currently scraped via news RSS, not deep-indexed.

### P3 — observability + UX

1. **Audit trail explorer** — chat audit table on aria-brain shows last 10 entries; full chronological browse + filter by entity / verdict / mode. ~4 hours.
2. **Response quality A/B board** — pick a batch of historical chats, re-run them through the current system, diff the responses, surface drift. ~6 hours.
3. **Ingest health gauge** — "is the spider actually fetching things?" The dashboard shows queue depth 337 but 24h fetches 0 — needs a clearer signal.
4. **What-changed-since-last-time toast** — first time an operator opens aria-brain after a deploy, surface "12 R-numbers shipped since your last visit. Top change: Layer 8 counter-intel now wired into every DD."

---

## 7 — Architecture summary (for the assessment record)

**Servers**
- fly.io — Python `aria_service/` (uvicorn / fastapi). Persistent volume at `/data` for knowledge + ledger + RAG (chromadb).
- seenode — Node `server.mjs` (Express 5.1). All public HTML + auth + proxy to fly via `app.use('/api/aria', ...)` middleware.

**LLM chain (ROBUST — 4 paths)**
- Anthropic (Claude Sonnet 4.6) → DeepSeek → Groq → ARIA-LLM (when `ARIA_LLM_URL` set, currently dormant)
- Wrapped in MeteredProvider (R-F131 now extracts real provider from `result.routed_via`)
- Wrapped in RateLimiter (50 rpm, 10 burst)

**Substrate**
- Disk-first (`/data/aria_*.json` canonical, Redis convenience mirror with snapshot-gzip in R-F36)
- 100-year retention sentinel (no TTLs anywhere) — pay-once-remember-forever doctrine
- Off-host email shipping LIVE → operator inbox (daily backup ~438KB)

**Constitutional discipline (23 clauses)**
- Clause 1-6: identity / honesty / clarity / refusal
- Clause 7-11: source-tier / sanctions / fabrication
- Clause 12: PARTIAL EXTRACTION discipline
- Clauses 13+14: current event / propaganda quarantine
- Clauses 15-18: deception detection / red-teaming / negotiation / commitment
- Clauses 19-23: SAR trigger / autonomy gates / safety / refusal-to-confabulate / ticketing

**Calibration: ACCEPTABLE**
- Mastery (self-assessed): 88%
- Accuracy (ground truth): 76%
- Delta: 11.9pp (overconfident — within tolerated band, no auto-tune needed)
- Brier score 0.153 → 15% confidence reduction applied

**Governance**
- L3 FULL autonomy (with auto-allowed / draft-only / never-auto bands)
- Hard cap $300/month (4.1% utilised; projected $41.74 — well below)
- 23-clause constitution adherence checked per chat turn
- 10 pending adversarial amendments (operator triage required)

---

## 8 — Bottom line

ARIA is **production-ready for Arkmurus internal use today**. The 16 R-numbered improvements shipped in 2026-05-10 closed three classes of issue:

1. **DD depth** (10 layers, 3 of which were dormant modules now wired)
2. **Search reach** (8+ backends, 11-language fan-out, defence-event calendar, memory-first inversion)
3. **Operational hygiene** (cost attribution, dashboard layout, false-positive filtering, auto-language gap)

She can self-diagnose 33 modules including all 9 added today. She cannot yet self-diagnose her own code-change history (no SPRINT-DIGEST task) or measure per-layer / per-backend effectiveness — both queued as P1 items, ~6 hours combined.

Cost trajectory at 4.1% MTD utilisation is comfortable; resilience floor is ROBUST with 4 independent LLM paths. The single biggest external dependency reducing capability today is the Brave Search API top-up (operator action), which would lift general-web search quality materially.

**Suggested operator priorities for 2026-05-11**:
1. Top up Brave (5 min)
2. Investigate 3 quarantined DDs (15 min)
3. Reject 10 stale amendments (5 min)
4. Set 4 graceful-degrade env vars (10 min)

Total: ~35 min of operator hygiene → unblocks meaningful capability lift.
