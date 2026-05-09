# ARIA — Full System Architecture & Capability Reference (2026-05-10)

**HEAD at writing**: `c547d7f`
**Operator**: Antonio (Arkmurus)
**Purpose**: single canonical reference for how ARIA is wired end-to-end, how every component links to her brain, and what she can do.

---

## 1 — System architecture (top-level)

```
                        ┌──────────────────────────────────────┐
                        │   CLIENT SURFACES                    │
                        ├──────────────────────────────────────┤
                        │ • Web UI — intel.arkmurus.com        │
                        │   (chat / DD / explorer / aria-brain)│
                        │ • WhatsApp listener (Baileys)        │
                        │ • Email (oxoffice IMAP/SMTP)         │
                        │ • Public REST API (R-F42 scaffold)   │
                        └──────────────┬───────────────────────┘
                                       │ HTTPS
                                       ▼
                  ┌────────────────────────────────────────────┐
                  │   EDGE — seenode (Node 20 + Express 5.1)   │
                  │   server.mjs · public/* · auth · proxy     │
                  ├────────────────────────────────────────────┤
                  │ • Auth (JWT, bcrypt user store)            │
                  │ • Static asset serving                     │
                  │ • app.use('/api/aria', requireAuth, proxy) │
                  │ • Sweep monitor (47 Node-side sources)     │
                  │ • WhatsApp listener + 15-min watchdog      │
                  └──────────────┬─────────────────────────────┘
                                 │ Bearer-token proxy
                                 ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │   BACKEND — fly.io (FastAPI / uvicorn / Python 3.13)            │
   │   aria_service/ — 477 routes, 10-layer DD, 11-language search   │
   ├──────────────────────────────────────────────────────────────────┤
   │  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────────┐ │
   │  │ aria_engine  │ │  routes/     │ │  intel/ — 100+ modules   │ │
   │  │ (chat path,  │ │  aria.py     │ │  (every defence-DD       │ │
   │  │  23-clause   │ │  dd.py       │ │   capability)            │ │
   │  │  constitution│ │  ...         │ │                          │ │
   │  └──────┬───────┘ └──────┬───────┘ └──────┬───────────────────┘ │
   │         │                │                │                       │
   │         ▼                ▼                ▼                       │
   │  ┌────────────────────────────────────────────────────────────┐  │
   │  │   BRAIN HOOK — single absorption gateway                   │  │
   │  │   brain_hook.absorb(module, summary, success, ...)         │  │
   │  └──────┬─────────────────────────────────────────────────────┘  │
   │         │ fans out to 5 substrates                                │
   │         ▼                                                          │
   │  ┌────────┐ ┌─────────┐ ┌──────────┐ ┌────────────┐ ┌───────┐    │
   │  │KNOWLEDGE│ │  LEDGER │ │   RAG    │ │   NEURAL   │ │ mem0  │    │
   │  │ 20,604  │ │ 19,633  │ │  76,632  │ │  10,715    │ │  195  │    │
   │  │  facts  │ │ signals │ │  chunks  │ │  neurons   │ │ facts │    │
   │  └────┬────┘ └────┬────┘ └────┬─────┘ └─────┬──────┘ └───┬───┘    │
   │       │           │           │              │             │      │
   │       └───────────┴───────────┴──────────────┴─────────────┘      │
   │                              │ disk-first persistence              │
   │                              ▼                                      │
   │  ┌──────────────────────────────────────────────────────────┐     │
   │  │  /data persistent volume + Upstash Redis (snapshot mirror)│     │
   │  │  • aria_knowledge.json  · aria_signals.json               │     │
   │  │  • aria_rag/ chromadb   · aria_neural.json                │     │
   │  │  • Upstash cluster: adapted-ostrich-92296 (eviction OFF)  │     │
   │  │  • Daily off-host email backup → operator inbox           │     │
   │  └──────────────────────────────────────────────────────────┘     │
   │                                                                     │
   │  ┌──────────────────────────────────────────────────────────┐     │
   │  │  LLM CHAIN — MeteredProvider → RateLimiter → Fallback    │     │
   │  │  Anthropic Claude Sonnet 4.6 → DeepSeek → Groq           │     │
   │  │  → ARIA-LLM (dormant; activates when ARIA_LLM_URL set)   │     │
   │  │  Cost meter: cost_tracker.record_call (per provider)     │     │
   │  └──────────────────────────────────────────────────────────┘     │
   │                                                                     │
   │  ┌──────────────────────────────────────────────────────────┐     │
   │  │  AUTONOMOUS ENGINE — 74 tasks, L3 FULL autonomy          │     │
   │  │  • research scheduler (30 min)                            │     │
   │  │  • self-improvement (2 h)                                 │     │
   │  │  • student loops (3 h / 6 h / 24 h)                       │     │
   │  │  • proactive watch (hourly)                               │     │
   │  │  • weekly report (Mon 06-08 UTC)                          │     │
   │  │  • watchlist re-screen (daily)                            │     │
   │  │  • tender monitor (6 h)                                   │     │
   │  │  • self-diagnostic (15 min)                               │     │
   │  └──────────────────────────────────────────────────────────┘     │
   └──────────────────────────────────────────────────────────────────┘
                                 │ outbound
                                 ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │   EXTERNAL SERVICES                                               │
   ├──────────────────────────────────────────────────────────────────┤
   │  Search:  Brave · SearXNG · DDG · Google News · Bing News · Academic    │
   │  Sanctions: OpenSanctions · OFAC · OFSI · UN SC · World Bank · ACLED   │
   │  Registries: SEC EDGAR · Companies House · 13 national adapters        │
   │  Trade-show: 18 defence-event sites (SAHA / IDEX / Eurosatory / DSEI...)│
   │  Auth/Identity: Wikidata · OpenCorporates · ICIJ Offshore Leaks        │
   │  Misc: Wayback Machine · ArXiv · OpenAlex · Crossref · Sentinel-2 (planned)│
   └──────────────────────────────────────────────────────────────────┘
```

### Two-server reality

- **fly.io** runs the brain (Python). Persistent volume mounted at `/data` for knowledge / ledger / RAG / neural / mem0 + daily snapshots.
- **seenode** runs the edge (Node Express 5.1). Public HTML, JWT auth, Node-side source sweep monitor, WhatsApp listener, proxies every `/api/aria/*` to fly via `app.use('/api/aria', requireAuth, ariaProxy)` middleware (R-F117 catch-all pattern, Express-5 compatible).
- Both auto-deploy from `git push origin main`. Commits affecting only `aria_service/` deploy fly only; commits touching `public/` or `server.mjs` deploy seenode only.

---

## 2 — Brain wiring (the 5 substrates + the absorption gateway)

```
                    ┌───────────────────────────────┐
                    │  brain_hook.absorb(...)       │
                    │  Single entry point — every   │
                    │  feature module calls this    │
                    │  exactly once per fact.       │
                    └───────────┬───────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        │                       │                        │
        ▼                       ▼                        ▼
   ┌─────────┐           ┌──────────┐              ┌─────────┐
   │KNOWLEDGE│           │ NEURAL   │              │ MASTERY │
   │ disk    │           │ memory   │              │ tracker │
   │ JSON    │           │ Hebbian  │              │ EWMA    │
   │ store   │           │ co-act.  │              │ per     │
   │ R-F1    │           │ 10,715   │              │ topic + │
   │ gzip    │           │ neurons  │              │ region  │
   └────┬────┘           └────┬─────┘              └────┬────┘
        │                     │                          │
        ▼                     ▼                          ▼
   contradictions        edge-group           CORE_MASTERY 10 tags
   detected              auto-pruning         (langs + sanctions +
   superseded            R-F92 LoRA           NATO + strat geo +
   fact tracking         training corpus      export control)

   In parallel, brain_hook ALSO routes to:
        ┌─────────┐  ┌─────────┐  ┌──────────────┐
        │ INTEL   │  │  RAG    │  │   mem0       │
        │ LEDGER  │  │ chunks  │  │ personal     │
        │ time-   │  │ chromadb│  │ notebook     │
        │ series  │  │ semantic│  │ (operator    │
        │ signals │  │ recall  │  │  scoped)     │
        │ R-F36   │  │ 800-tok │  │              │
        │ gzip    │  │ chunks  │  │              │
        └─────────┘  └─────────┘  └──────────────┘
```

### The 5 substrates in detail

| Substrate | Volume | Storage | Role | Persistence |
|---|---|---|---|---|
| **Knowledge** | 20,604 facts | `/data/aria_knowledge.json` (canonical) + Upstash gzip mirror | Topic-keyed extracted facts. Deduplicated, contradiction-detected, verification-stamped. | Disk-first; Redis snapshot for fast read; daily email backup |
| **Intel ledger** | 19,633 signals | `/data/aria_signals.json` + Upstash gzip mirror | Time-series raw signals from sweeps + DDs + chats. Every fact source-of-truth. | Disk-first; Redis snapshot |
| **RAG store** | 76,632 chunks (17,600 docs + 59,031 fact embeddings) | `/data/aria_rag/` chromadb persistent | Semantic retrieval substrate. Embedded with `all-MiniLM-L6-v2`. 800-token chunks, 150-token overlap. | Disk only; chromadb's own format |
| **Neural memory** | 10,715 neurons / 9,279 edge groups | `/data/aria_neural.json` | Hebbian co-activation graph. Concepts that fire together wire together. Drives associative recall. | Disk + Redis |
| **mem0** | 195 facts | per-session keyed | Personal notebook scoped to a single chat session (operator-attached). Short-lived, high-context. | Redis |

### How knowledge becomes "known"

A fact enters the brain through one of these gates:

1. **Chat ingest** — operator (or counterparty via WhatsApp) says something → `aria_engine.absorb_chat_input()` → brain_hook.
2. **Document upload** — PDF/DOCX/XLSX → `document_intelligence.extract()` → fact extraction → brain_hook.
3. **Sweep** — autonomous research / spider / RSS feeds → `intel_ledger.add_signal()` → brain_hook.
4. **DD orchestrator** — every DD run absorbs its findings → brain_hook.
5. **Self-quiz** — student loop generates Q&A pairs from existing corpus → brain_hook (low-confidence absorption).
6. **Knowledge-pack seed** — curated injection (R-F141) → `knowledge.store_fact()` → brain_hook.

Every gate writes to **all 5 substrates** in parallel. No "secondary" brain — they're siblings.

### Pay-once-remember-forever doctrine

100-year retention sentinel across all substrates (no TTLs anywhere). When ARIA pays Brave / Anthropic / Upstash for a piece of intel, it stays in the substrate forever. Repeat queries hit memory first (R-F124 memory-first inversion); web fetch only fires for what's genuinely new.

---

## 3 — DD pipeline (10 layers, sequential, fail-open)

```
                    target = {"name": "...", "country": "..."}
                                    │
                                    ▼
   ╔═════════════════════════════════════════════════════════════════╗
   ║   orchestrate_dd(target)  — 10-layer ARK-DD methodology         ║
   ╠═════════════════════════════════════════════════════════════════╣
   ║                                                                  ║
   ║   1. IDENTITY   (60s timeout, fail-open)                        ║
   ║      ├─ Entity name normalisation + jurisdiction inference       ║
   ║      ├─ Registry pull (13 national adapters + OpenCorporates)    ║
   ║      ├─ Sanctions screen (OpenSanctions + 6 primary sources)     ║
   ║      └─ Ghost score (28 indicators, company-only)                ║
   ║                          │                                       ║
   ║   2. NETWORK    (30s, fail-open)                                ║
   ║      ├─ Officer / director / UBO graph                           ║
   ║      ├─ R-F76 RCA (relatives + close associates)                 ║
   ║      └─ PEP cross-reference                                      ║
   ║                          │                                       ║
   ║   3. COMPLIANCE (30s, fail-open)                                ║
   ║      ├─ Export control classification (EUC / ECCN / Wassenaar)   ║
   ║      ├─ R-F53 EUC library (12 markets)                           ║
   ║      ├─ R-F54 weapon catalogue (105 systems)                     ║
   ║      └─ R-F55 NDAA / MCF                                         ║
   ║                          │                                       ║
   ║   4. DIGITAL    (60s, fail-open)                                ║
   ║      ├─ deep_research crawl                                      ║
   ║      ├─ Press coverage source-tier breakdown                     ║
   ║      ├─ R-F75 link-investigator + provenance lineage             ║
   ║      └─ Multimodal: image/PDF deep ingest                        ║
   ║                          │                                       ║
   ║   5c. COMMERCIAL COHERENCE (10s, fail-open)                     ║
   ║      ├─ Payment-norm anomaly detection                           ║
   ║      ├─ Licence-chain plausibility                               ║
   ║      └─ Jurisdiction-specific corporate rules                    ║
   ║                          │                                       ║
   ║   5b. DECEPTION SCORING (Clause 16, inline)                     ║
   ║      └─ ARIADeceptionAnalyser over counterparty text + Layer-5c  ║
   ║         anomalies + digital-layer findings                       ║
   ║                          │                                       ║
   ║   6. VERIFICATION (30s, fail-open)                              ║
   ║      ├─ Confidence floor (worst across all layers)               ║
   ║      ├─ Conflict detection (classification mismatches)           ║
   ║      └─ Grounded-rate score                                      ║
   ║                          │                                       ║
   ║   7. SYNTHESIS (10s, fail-open)                                 ║
   ║      ├─ ACH matrix (R-F71 explainability)                        ║
   ║      ├─ Final ghost classification                               ║
   ║      └─ Risk classification (GREEN/AMBER-LIGHT/AMBER/AMBER-DEEP/ ║
   ║         RED/HARD_STOP)                                           ║
   ║                          │                                       ║
   ║   8. COUNTER-INTELLIGENCE  ★ R-F121 (8s, fail-open)             ║
   ║      ├─ Narrative-shift detection                                ║
   ║      ├─ Coordinated press identification                         ║
   ║      └─ Tier 1 vs Tier 3 contradiction flag                      ║
   ║                          │                                       ║
   ║   9. SANCTIONS DIVERGENCE  ★ R-F122 (10s, fail-open)            ║
   ║      ├─ Cross-list jurisdictional gap                            ║
   ║      └─ "Listed by US/UK but NOT EU/UN" narrative                ║
   ║         + R-F133 token-overlap filter (drops unrelated matches)  ║
   ║                          │                                       ║
   ║  10. FORENSIC  ★ R-F123 (inline, fail-open)                     ║
   ║      ├─ Benford's Law on procurement values (≥50 pts)            ║
   ║      └─ TBML transaction classifier (per-line)                   ║
   ║                          │                                       ║
   ║   ─── BLUF assembly + render_markdown ───                       ║
   ║                          │                                       ║
   ║   Verification gate (RED only): second-opinion via different     ║
   ║   provider; CRITICAL_UNVERIFIED if disagreement, VERIFIED-BY-    ║
   ║   DISAGREEMENT if both reach same verdict                        ║
   ║                          │                                       ║
   ║   Brain absorb: dd_orchestrator → all 5 substrates               ║
   ╚═════════════════════════════════════════════════════════════════╝
                                    │
                                    ▼
                       ARKDDReport — JSON + Markdown render
                       Stored: Redis crucix:dd:report:{run_id}
                       Indexed: crucix:dd:report_index (R-F130)
                       Watermarked + HMAC signed (clause 18)
```

### Why 10 layers, not just one mega-prompt

- **Each layer has its own timeout + fail-open** — one failure doesn't kill the report
- **Sequential dependency** — synthesis can't run until verification finishes; layer 8/9/10 can run after deception because they're independent of it
- **Per-layer evidence** — operator audit trail traceable per finding to a layer
- **Constitutional clauses 7+8+11+16+20** all anchor to specific layers

---

## 4 — Search backbone (memory-first, 8+ backends, 11-language)

```
                  search(query, language='en')
                              │
                              ▼
               ┌─ R-F124: query memory FIRST ─┐
               │  (rag_store.search 4s timeout)│
               │   results get +0.5 relevance  │
               └───────────────┬───────────────┘
                               │
                               ▼
              ┌─────────────── parallel gather ────────────────┐
              │                                                │
              ▼                                                ▼
   ┌───────────────────┐                          ┌──────────────────┐
   │ 7 BASE BACKENDS   │                          │ R-F125 / R-F135 │
   │  (always fire)    │                          │ AUTO-LANGUAGE    │
   ├───────────────────┤                          │ FAN-OUT          │
   │ 1. Brave          │                          ├──────────────────┤
   │    + cost-track   │                          │ Detect Unicode   │
   │    R-F139         │                          │ script + native  │
   │ 2. SearXNG        │                          │ markers + English│
   │ 3. DuckDuckGo     │                          │ country names    │
   │ 4. Google News    │                          │                  │
   │ 5. Bing News      │                          │ Languages:       │
   │ 6. Academic       │                          │  tr ko ja zh hi  │
   │    (3 sub:        │                          │  ru ar pt es     │
   │    OpenAlex /     │                          │  fr de           │
   │    Semantic /     │                          │                  │
   │    CrossRef)      │                          │ For each detected│
   │ 7. R-F126         │                          │ lang, add 2 more │
   │   Defence-event   │                          │ backend calls    │
   │   calendar        │                          │ (Brave + Google  │
   │   (18 events)     │                          │  News in lang).  │
   └───────────────────┘                          │ Cap 3 extra langs│
                                                  └──────────────────┘
                              │
                              ▼
                   asyncio.gather(*backends)
                   raw_results: list[list[SearchResult]]
                              │
                              ▼
                   ┌───────────────────────┐
                   │ DEDUPE by URL         │
                   │ TRIANGULATION boost   │
                   │ (+0.3 if 2+ backends) │
                   │ CREDIBILITY filter    │
                   │ (≤ tier 5)            │
                   │ DISINFO quarantine    │
                   │ (tier 6 tagged)       │
                   │ RELEVANCE re-rank     │
                   └───────────┬───────────┘
                               │
                               ▼
                   final results → caller
                   + brain_hook absorb (web_search topic)
```

### Wayback fallback (R-F126)

`extract_url_text` and `read_article` both trip Wayback Machine fallback on **401/402/403/404/410/451/500/502/503/504**. Post-event PR pages routinely 404 once the OEM CMS rotates content; archive.org snapshot recovers the contract data.

### Defence-event calendar (R-F126) — 18 events

`SAHA / IDEX / NAVDEX / Eurosatory / DSEI / AUSA / Dubai Airshow / Le Bourget / Indo Defence / LIMA / DX Korea / AAD / Expodefensa / WDS Riyadh / ILA Berlin / Balt Military / Milipol / LIMA Maritime`. When the query matches, ARIA auto-routes a `site:`-scoped search against the official event domain in the local language.

---

## 5 — Autonomous engine (74 tasks, L3 FULL)

```
   AUTONOMY DOCTRINE — aria_autonomy_doctrine.md
   ┌─────────────────────────────────────────────────────────┐
   │ AUTO-ALLOWED                                            │
   │ • Internal research, brain absorption, RAG ingest       │
   │ • Self-improvement scans, mistake_ledger writes         │
   │ • Watchlist re-screen, sanctions sweep, tender monitor  │
   │ • DD on operator-supplied entities                      │
   ├─────────────────────────────────────────────────────────┤
   │ DRAFT-ONLY (operator must approve)                      │
   │ • External email send                                   │
   │ • Client-facing DD reports leaving the platform         │
   │ • Constitution amendments                               │
   │ • Codegen / self-improve auto-deploys                   │
   ├─────────────────────────────────────────────────────────┤
   │ NEVER-AUTO (hard rule)                                  │
   │ • Auto-spend (LLM cap enforced + Brave fail-open)       │
   │ • Auto-public-post                                      │
   │ • Name fabrication (clause 11)                          │
   └─────────────────────────────────────────────────────────┘

   SCHEDULER LOOPS (always running)

   ┌──────────────────────────┬───────────┬────────────────────────────────┐
   │ Loop                     │ Cadence   │ What it does                   │
   ├──────────────────────────┼───────────┼────────────────────────────────┤
   │ research_scheduler       │ 30 min    │ ~30 RSS feeds + spider crawl   │
   │ self_improve             │ 2 h       │ Code-scan + mistake-ledger     │
   │ student_self_quiz        │ 3 h       │ Quiz against existing corpus   │
   │ student_reading          │ 6 h       │ Random article read + absorb   │
   │ library_consolidate      │ 24 h      │ Reasoning library curation     │
   │ proactive_watch          │ 1 h       │ Daily briefing prep            │
   │ weekly_report            │ Mon 06-08 │ Weekly meta-review             │
   │ watchlist_rescreen       │ Daily     │ All watched entities re-DD     │
   │ tender_monitor           │ 6 h       │ 5-portal tender sweep          │
   │ self_diagnostic          │ 15 min    │ 42-module health check (R-F136)│
   │ source_uptime            │ Daily 02h │ Ping all 188 catalogue sources │
   │ counter_intel_weekly     │ Weekly    │ Top-5 entities scanned (R-F84) │
   │ adversarial_weekly       │ Weekly    │ 11-attack red-team suite       │
   │ training_export          │ Daily     │ Harvest chat → SFT pairs       │
   │ memory_replication       │ Daily     │ /data → email backup           │
   └──────────────────────────┴───────────┴────────────────────────────────┘
```

74 distinct tasks loaded from `aria_service/autonomous/tasks.yaml`. Engine ticks every 60s after a 90s startup delay. Each task has a dedupe window so a fired task won't refire within its window.

---

## 6 — Self-introspection surface (42 modules monitored)

```
   /api/aria/diagnostic/details     ─→  42-module health check
                                        each module asserted on:
                                          1. IMPORT  ✓
                                          2. ENTRYPOINT  ✓
                                          3. BRAIN_REGISTERED (where applicable)  ✓
                                          4. ROUTED (endpoint reachable)  ✓
                                          5. SCHEDULED (task in tasks.yaml)  ✓
                                          6. CREDENTIALED (env var set)  ✓
                                          7. RESPONSIVE (smoke test)  ✓

   42 modules covered:
   ─────────────────────────────────────────────────────────────────
   FIRE-ON-ARIA         scratchpad / comprehension / consistency_suite
                        capability_card / calibration_auto_tune

   ANTI-FABRICATION     ground_truth_guard / tool_claim_guard /
                        commitment_guard / query_decomposer /
                        known_publisher_router

   SOURCE STACK         source_uptime_monitor / defence_source_seed /
                        rlaif / critique_collector

   PRIMARY DATA         sec_edgar / ofac_sdn / fcdo_sanctions /
                        un_sc_sanctions / worldbank_debarred /
                        worldbank_documents / acled / vendor_registry

   PIPELINE             pending_actions / brain_hook / autonomous_engine /
                        researcher.extract_url_deep / deep_researcher.crawl /
                        crawl_enhancements

   SCRAPER STACK        scraper_playwright / scraper_orchestrator /
                        scraper_procurement / scraper_generic

   INTEGRATIONS         airtable_sync

   R-F136 ADDITIONS     web_search ★ / intel_ledger ★ / dd_orchestrator ★ /
                        coverage_heatmap / learning_progress /
                        counter_intelligence / sanctions_divergence /
                        forensic_benford / tbml_detection
                        (★ = CRITICAL — block alerts on RED)
```

### Other introspection endpoints

| Question | Endpoint | Frequency |
|---|---|---|
| Am I overconfident? | `/calibration/review` | nightly |
| Am I being attacked? | `/adversarial/stats` | weekly |
| Mastery per topic / region? | `/student/mastery/heatmap` | continuous |
| What's my LLM spend trajectory? | `/cost/monthly` | continuous |
| External-service spend (Brave / Upstash)? | `/cost/external` (R-F139) | continuous |
| Where are my knowledge gaps? | `/learning/coverage/gaps` | continuous |
| What did I learn in 24h? | `/learning/freshness` | continuous |
| Operating-mode triggers? | `/operating-mode` | continuous |
| Resilience floor? | `/autonomy/surface` | continuous |

---

## 7 — Capability inventory (full taxonomy)

### A — Identity & sanctions

| Capability | Module | Surface | Status |
|---|---|---|---|
| Sanctions screen (multi-list aggregator) | `sanctions.py` | `/compliance/screen` | live, 6 primary + OpenSanctions |
| Sanctions divergence (cross-list narrative) | `sanctions_divergence.py` | `/sanctions/divergence` | R-F68/F122/F133 |
| RCA / relatives recursive screen | `rca_screening.py` | `/compliance/rca` | R-F76 |
| Ghost score (28 indicators) | `ghost_detection.py` | DD Layer 1 | live |
| Counter-intelligence scan | `counter_intelligence.py` | `/security/counter-intel/scan` | R-F84/F121 |
| Beneficial-owner chain | `network_walker.py` | DD Layer 2 | live |
| PEP cross-reference | `sanctions.py` (PEP topics) | DD Layer 2 | live |

### B — Compliance & export control

| Capability | Module | Surface | Status |
|---|---|---|---|
| Export control classifier | `export_control.py` | DD Layer 3 | live |
| ECCN classification | `eccn_classifier.py` | tool surface | R-F54 |
| EUC library (12 markets) | `euc_library.py` | DD Layer 3 | R-F53 |
| Weapon catalogue (105 systems) | `weapon_catalogue.py` | DD Layer 3 | R-F54 |
| NDAA / MCF | `ndaa_mcf.py` | DD Layer 3 | R-F55 |
| Wassenaar dual-use | catalogue + `eccn_classifier` | DD Layer 3 | live |
| FATF typology matcher | `fatf_typology.py` | `/fatf/match` | R-F72 |
| Economic substance (BEPS) | `economic_substance.py` | `/economic-substance` | R-F77 |
| Crypto wallet screen | `crypto_sanctions.py` | `/crypto/screen` | R-F74 |

### C — Forensic & financial

| Capability | Module | Surface | Status |
|---|---|---|---|
| Benford's Law | `forensic_benford.py` | `/forensic/benford` | R-F70 |
| TBML transaction classifier | `tbml_detection.py` | `/tbml/classify` | R-F73 |
| FCPA enforcement scan | `fcpa_enforcement.py` | autonomous task | R-F69 |
| Provenance lineage | `provenance_lineage.py` | `/provenance/lineage` | R-F75 |
| Citation audit | `citation_audit.py` | `/citation/verify` | R-F78 |

### D — Search & retrieval

| Capability | Module | Surface | Status |
|---|---|---|---|
| 8-backend parallel search | `web_search.py` | `search()` | R-F124/F125/F126/F135 |
| Memory-first inversion | `web_search._query_memory` | inline | R-F124 |
| Auto-language fan-out (11 lang) | `web_search._detect_query_languages` | inline | R-F125/F135 |
| Defence-event calendar (18 events) | `web_search._search_defence_event` | inline | R-F126 |
| Wayback fallback | `crawl_enhancements.fetch_via_wayback` | inline | live + R-F126 widening |
| RAG retrieval | `rag_store.search` | inline / `/rag/search` | live |
| Brave Answers (paid) | `brave_answer.py` | `/brave/answer` | live + R-F139 cost track |
| Web Atlas (188 source registry) | `web_atlas.py` + `defence_source_seed.py` | `/atlas/stats` + `/sources/seed/catalogue` | R-F137 |

### E — Document intelligence

| Capability | Module | Surface | Status |
|---|---|---|---|
| Multimodal vision (chunked PDF) | `document_intelligence.py` | upload route | live |
| OCR (Tesseract + EasyOCR removed) | `ocr.py` | inline | live |
| Image embedding (CLIP — planned) | — | — | P2 backlog |
| Forgery / AI-content detection | — | — | P2 backlog |

### F — Network & relationships

| Capability | Module | Surface | Status |
|---|---|---|---|
| Officer / director graph | `network_walker.py` | DD Layer 2 | live |
| OEM contact graph | `oem_contact_graph.py` | `/oem/contacts` | R-F138 (215 slots) |
| Watchlist auto-rescreen | `watchlist.py` | `/dd/watchlist/*` | live |
| Pipeline (BD CRM-lite) | `pipeline.py` | `/pipeline/*` | live |
| Contact intelligence | `contact_intel.py` | `/contacts/*` | live |

### G — Autonomous learning

| Capability | Module | Surface | Status |
|---|---|---|---|
| Mastery tracker (EWMA per topic+region) | `mastery.py` | `/student/mastery/heatmap` | live |
| Coverage heatmap (17 domains × 51 jurisdictions) | `coverage_heatmap.py` | `/learning/coverage` | R-F89/F128 |
| Domain freshness | `learning_progress.py` | `/learning/freshness` | R-F88 |
| Self-quiz student loop | `student_loops.py` | autonomous | live |
| Reading student loop | `student_loops.py` | autonomous | live |
| Knowledge pack seeding | `knowledge_packs/*` | `/knowledge/seed-*` | R-F141 |
| Capability gaps tracker | `capability_gaps.py` | `/capability-gaps` | live |

### H — Self-awareness

| Capability | Module | Surface | Status |
|---|---|---|---|
| Self-diagnostic (42 modules) | `self_diagnostic.py` | `/diagnostic/details` | R-F136 |
| Capability manifest (auto-derived) | `capability_manifest.py` | `/capability/manifest` | live |
| Capability card | `capability_card.py` | `/capability-card` | live |
| Self-metrics | `self_metrics.py` | `/self/metrics` | live |
| Predictor blocking gate | `predictor.py` | `/predictor/block_rate` | live |
| Mistake ledger | `mistake_ledger.py` | `/self/mistakes/recent` | live |
| Calibration auto-tune | `calibration_auto_tune.py` | `/calibration/auto-tune` | live |

### I — Verification & trust

| Capability | Module | Surface | Status |
|---|---|---|---|
| Verification gate (RED 2nd opinion) | `verification_gate.py` | inline | live |
| Verified intel (Clause 17) | `verified_intel.py` | inline | live |
| Source verifier | `source_verifier.py` | inline | live |
| Source uptime monitor | `source_uptime_monitor.py` | `/sources/uptime` | live |
| Adversarial test suite | `adversarial_challenge.py` | `/adversarial/*` | 11 attacks, 90.9% pass |
| Counter-intelligence (R-F84) | `counter_intelligence.py` | DD layer 8 + tool | R-F84 / R-F121 |
| Constitution enforcement | `aria_engine.py:CONSTITUTION` | every chat | 23 clauses |

### J — Cost & resilience

| Capability | Module | Surface | Status |
|---|---|---|---|
| LLM cost tracker | `cost_tracker.py` | `/cost/monthly` | live |
| External-service cost tracker | `cost_tracker.record_external_call` | `/cost/external` | R-F139 |
| Brave cost wrapper | `cost_tracker.record_brave_call` | inline | R-F139 |
| Upstash usage probe | `routes/aria.py:_upstash_usage_probe` | `/cost/external` | R-F139 |
| Circuit breakers | `circuit_breaker.py` | `/circuit-breakers` | live, ~12 named breakers |
| Rate limiter | `rate_limiter.py` | inline (50 rpm, 10 burst) | live |
| Operating mode | `operating_mode.py` | `/operating-mode` | NORMAL ↔ DEGRADED ↔ SUPERVISED |
| Memory replication | `memory_replication.py` | autonomous daily | live (off-host email) |

### K — Operator surfaces

| Surface | Path | Purpose |
|---|---|---|
| Web chat | `/aria.html` | Primary chat — streaming + voice + persona |
| Explorer | `/explorer.html` | One-query OSINT pivot — 6 tabs |
| DD reports | `/dd-reports.html` | DD library + 12 pipeline tools |
| ARIA Brain | `/aria-brain.html` | Health / quality / cost / coverage / autonomy |
| Sources | `/sources.html` | Source health + catalogue (R-F137/R-F143) + OEMs |
| Status | `/status.html` | Public uptime page |
| Model card | `/model-card.html` | Constitutional + capability disclosure |
| Pipeline | `/pipeline.html` | BD CRM |
| Watchlist | `/watchlist.html` | Watched-entity table |
| Audit | `/audit.html` | Chat audit + compliance trail |
| WhatsApp | Baileys listener | Same brain over WA |
| Email | oxoffice IMAP/SMTP | Inbound email → RAG; outbound digest |

---

## 8 — The 23 constitutional clauses (one-line summary)

| # | Clause | What it binds |
|---|---|---|
| 1 | Identity | "I am ARIA, defence-broking advisor" |
| 2 | Honesty | No invention of facts not in the substrate |
| 3 | Source-tier | Cite source + tier on every claim |
| 4 | Refuse-when-uncertain | Don't fill gaps with confident-sounding text |
| 5 | Clarity | One claim per sentence; ambiguity flagged |
| 6 | Persona-aware | Match operator's role / sector / sophistication |
| 7 | Sanctions discipline | Always check before naming an entity |
| 8 | Sanctions sources | OFAC + OFSI + UN SC + EU + others, never any one alone |
| 9 | Refuse-on-failed-fetch | "Source returned empty → I refuse to claim" |
| 10 | Output guards | Officeholder / commitment / tool-claim / propaganda checked per turn |
| 11 | Anti-fabrication | Names / dates / contracts / quantities NEVER invented |
| 12 | PARTIAL EXTRACTION discipline | Banner truncated docs explicitly |
| 13 | Current-event hygiene | "I don't know — no signal as of X" |
| 14 | Propaganda quarantine | Tier-D feeds dropped at ledger boundary |
| 15 | Deception detection | Run analyser on counterparty text |
| 16 | Red-team awareness | Adversarial framing detected + blocked |
| 17 | Verified intel provenance | Tier + verification + expiry on every fact |
| 18 | Source self-validation | Quality gate before web_atlas.add_source |
| 19 | SAR trigger | Suspicious-Activity-Report flag on threshold |
| 20 | Autonomy gates | Auto-allowed / draft-only / never-auto |
| 21 | Safety floor | Refusal-to-confabulate is always available |
| 22 | Ticket discipline | raise_ticket() not "I'll get back to you" |
| 23 | (latest) | Aspirational framing forbidden |

Constitution loaded into every chat system prompt. Refusals + adherence stamped on every chat audit row.

---

## 9 — How a chat turn flows (end-to-end example)

```
   Operator types: "DD on EDGE Group UAE"
   ─────────────────────────────────────────
   1. seenode: requireAuth → POST /api/aria/chat/stream → fly proxy
   2. fly: aria_engine receives chat input
   3. _prefetch_rag — query rag_store + intel_ledger for relevant context
                       (R-F107 contextvar threads sources to chat_audit)
   4. system prompt = 23-clause constitution + identity card + persona +
                       calibration card + RAG context + verified_intel
                       + conversation history
   5. Detect intent → "DD" → orchestrate_dd("EDGE Group", country="AE")
   6. Run 10 layers in sequence (each fail-open with timeout)
   7. R-F125 auto-language fan-out: detects "UAE" → adds Arabic Brave + Google News
   8. R-F126 defence-event calendar: not matched
   9. brain_hook absorbs DD result to all 5 substrates
   10. Verification gate (if RED): second-opinion via DeepSeek
   11. render_markdown produces the report
   12. cost_tracker records LLM spend per layer (R-F119 backfill)
   13. cost_tracker.record_brave_call records Brave API spend (R-F139)
   14. Stream response back via SSE
   15. chat_audit log captures: trace_id / sources / mastery / mode
   16. Brain_hook absorbs the chat itself (mastery EWMA bump if accurate)
```

For each turn, ARIA touches: 8 search backends, 5-7 LLM calls, 5 brain substrates, ~10 endpoints. Average chat-with-DD latency: 60-150s.

---

## 10 — What ARIA does not do (intentional limits)

- **Auto-spend** — hard cap $300/month; will refuse rather than overspend.
- **Auto-public-post** — never tweets / posts / publishes externally.
- **Auto-client-send** — drafts client-facing reports but ALWAYS holds for operator approval.
- **Name fabrication** — refuses to invent executive names even when pressed.
- **Aspirational framing** — refuses "I will research X" without a tool block proving research happened.
- **Single-source claims on Tier-D sources** — propaganda feeds quarantined at ingest.
- **Self-deploy code changes** — self-improve loop catalogues issues but operator manually deploys via CLI per autonomy doctrine.

These are constitutional constraints, not bugs. They are the moat — ARIA's competitors cut these corners; she does not.

---

## 11 — End state: how to use this document

1. **Onboarding**: read top-down to understand the platform.
2. **Capability lookup**: jump to §7 — the inventory is keyed by R-number for git archaeology.
3. **Architecture decisions**: §1 + §2 + §3 cover server split, brain wiring, DD pipeline.
4. **Health questions**: §6 lists every introspection endpoint.
5. **Operator hygiene**: §10 + clause 20 governance bands.
6. **Strategic direction**: see `aria_capability_expansion_roadmap_2026_05_10.md` for what to build next.

This document is the canonical reference as of 2026-05-10 HEAD `c547d7f`. R-F sequence preserved for auditability via `py scripts/sprint_metrics.py --since YYYY-MM-DD`.
