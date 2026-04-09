# ARIA System Evolution Log

This file tracks the structural transformation of ARIA over time so the
team can measure progress against the intelligence-system goals (Antonio's
mental module spec):

```
TIERS               = university education       (corpus_registry → chromadb)
WEB SEARCH          = daily newspaper             (sweep cycle + extract_url)
AUTONOMOUS RESEARCH = own investigations          (research_engine + proactive)
MEM0                = personal notebook           (auto-summariser)
OUTCOME TRACKING    = professional development    (feedback + eval + honesty)
```

Each entry records: **what changed, why, what failure mode it addresses, how
to verify success, and the cumulative impact on the five mental-module layers.**

> **Maintainer note:** add an entry on every commit that materially changes
> ARIA's behaviour, knowledge, or guardrails. Don't add entries for trivial
> tweaks (typos, comment polish) — only structural transformations.

---

## Layer scorecard — current snapshot (2026-04-09, end of session 2)

| Layer | Status | Score | What's missing |
|---|---|---|---|
| **TIERS** (corpus → chromadb) | ✅ Working | **85%** | Tier D distillation 70% complete; failed sources need retry; backfill from Redis facts not yet enabled |
| **WEB SEARCH** (sweep + crawl) | ✅ Working | **75%** | Static fetcher only — JS-rendered SPAs return thin content; propaganda blocked at boundary |
| **AUTONOMOUS RESEARCH** | 🟡 Built, gated off | **40%** | Code exists; `ARIA_AUTONOMOUS_RESEARCH_ENABLED=0` set as secret per field-test freeze |
| **MEM0** (personal notebook) | ✅ Working | **70%** | Per-turn summariser fires; needs decay + conflict resolution + better retrieval weighting |
| **OUTCOME TRACKING** | ✅ Working | **80%** | Stage 2 stack live (feedback, eval, source_verifier, honesty_judge); reaction handler validated |

**Aggregate readiness: ~70%** — up from ~25% three days ago.

---

## Constitution evolution (the LLM-side defence layer)

| Clause | Name | Added | Trigger incident |
|---|---|---|---|
| 1 | EPISTEMIC HONESTY | foundational | — |
| 2 | SOURCE INTEGRITY | foundational | — |
| 3 | COMPLIANCE FIRST | foundational | — |
| 4 | SELF-CRITICAL REASONING | foundational | — |
| 5 | COMMERCIAL REALISM | foundational | — |
| 6 | INTELLECTUAL COURAGE | foundational | — |
| 7 | KNOWING LIMITS | foundational | — |
| 8 | MEMORY & CONTINUITY | foundational | — |
| **9** | **NO PROFILING WITHOUT DATA** | 2026-04-08 | Omar J Jones IV LinkedIn confabulation |
| **10** | **OFFICEHOLDER DISCIPLINE** | 2026-04-08 | Ghana Nitiwul stale-officeholder |
| **11** | **TRUTH-IN-ACTION** | 2026-04-08 | Fabricated `/purgecases` confirmation |
| **12** | **NO DOCUMENT REVIEW WITHOUT TEXT** | 2026-04-09 (am) | CDL Hotels PDF "Ghana review" |
| **13** | **NO `[CONFIRMED]` ON UNCITED CURRENT EVENTS / PROPAGANDA / TOPIC BLEED** | 2026-04-09 (pm) | Vision International "Lebanon crisis response" |
| **14** | **NO FABRICATED VERIFIABLE FACTS** | 2026-04-09 (pm) | Modirum Gespi registry data fabrication |

The pattern: every clause is grounded in a **specific past incident** that the
team observed in production. Generic "intelligence tradecraft" language is
explicitly avoided in favour of incident-anchored prose so the LLM treats the
rule as a *concrete* failure to avoid, not an *abstract* principle to interpret.

---

## Conditional addenda (the LLM-side specialisation layer)

These are domain-specific prompt fragments that fire only when the user query
matches a specific intent. They are gated by env vars + intent detectors so
they don't burn token budget on irrelevant queries.

| Module | Fires when | Status | Trigger incident |
|---|---|---|---|
| `pmesii.py` | Country assessment intent + covered country | ✅ Live | Need for consistent country brief structure |
| `analytic_principles.py` | ALWAYS (Tier D distillation) | ✅ Live | Need for shared analytic tradecraft (Heuer / Tetlock / red-teaming) |
| `negotiation_principles.py` | Negotiation / approach / BATNA / counterparty intent | ✅ Live | Need for structured BD discipline |
| `correction_learner.py` | Recent user corrections present | ✅ Live | Need for user-correction memory |
| `stale_knowledge_alerts.py` | Country with known stale-officeholder risk | ✅ Live | Ghana Nitiwul incident |

---

## Code-side rails (the deterministic guardrails)

These are non-LLM defences that can't be talked around by a misbehaving model.

| Rail | Module | Purpose | Trigger incident |
|---|---|---|---|
| Bearer-token auth | `routes/aria.py` `require_aria_token` | Block unauthenticated access to all `/api/aria/*` | Yesterday's audit found 5 open holes |
| Hardcoded-fallback removal | `server.mjs` `requireAuth` | Block public-source `'aria-internal'` token impersonation | Phantom admins in audit log |
| Reaction-event ledger | `lib/whatsapp/waListener.mjs` `_sentMsgIds` | Identify reactions to ARIA messages without unreliable `fromMe` flag | Self-paired Baileys quirk |
| Document injection | `lib/whatsapp/waListener.mjs` `handleAriaMention` | Force attached PDF text into the chat envelope so the LLM has the verbatim content | CDL Hotels review fabrication |
| Intel-context relevance filter | `aria_engine.py` `_build_intel_context` | Drop intel signals that share <2 keywords with the user query | Lebanon bleed into Vision International RFQ |
| Propaganda-source blocking | `aria_engine.py` + `intel_ledger.py` | Tag known biased channels at retrieval AND block at ingest boundary | intelslava / CIG_telegram contamination |
| Fast `extract_url` tool | `intel/researcher.py` + `routes/aria.py` `_execute_tool` | Fetch + extract verbatim site content (no LLM, no RAG) and inject into chat envelope | Modirum Gespi crawl timeout + fabrication |
| Persistent chromadb volume | `fly.toml` `[mounts]` | chromadb data survives machine restarts | 615 chunks lost on first deploy |
| `/admin/purge-signals` endpoint | `routes/aria.py` | Surgical removal of polluted signals from intel ledger | Lebanon contamination cleanup |
| `/admin/audit-user-consistency` endpoint | `server.mjs` | Detect phantom admin actions in audit log | Missing-users-in-panel symptom |
| Markdown normaliser at WA boundary | `lib/whatsapp/waListener.mjs` `_normaliseForWhatsApp` | Rewrite `**bold**` → `*bold*` because LLMs ignore RESPONSE STYLE under load | Modirum reply with literal `**` chars |
| MEM0 personal-notebook auto-summariser | `intel/mem0.py` | Per-turn knowledge fact extraction with cost discipline | Missing mental-module layer |

---

## Cumulative timeline of major commits

### 2026-04-08 (yesterday) — Foundation: clauses 9-11, observability, reaction handler

| Time | Commit | What |
|---|---|---|
| morning | (multiple) | Stage 2 observability stack: feedback / eval / source_verifier / cost_tracker / trace_stream / honesty_judge |
| afternoon | `89cfa58` | Constitution clauses 9-10: NO PROFILING WITHOUT DATA, OFFICEHOLDER DISCIPLINE — Omar J Jones IV + Ghana Nitiwul |
| afternoon | `99a3f26`/`9ba7145`/`54c9527` | Officeholder guard hardening (window-based matching, zero-URL detection) |
| evening | `f976f10` | Red-teaming addendum + `negotiation_principles.py` + ingest_tier_a polish |
| evening | `36e1e07` | Constitution clause 12 (NO DOCUMENT REVIEW WITHOUT TEXT) + listener document injection |

### 2026-04-09 (today) — Clauses 12-14, persistent storage, MEM0, corpus loaded

| Time | Commit | What |
|---|---|---|
| morning | `8129202` | Reaction handler — sent-message ledger v1 |
| morning | `9c0830d` | 5 auth holes closed (fly.io ingest, brain/signal, /events SSE, /webhook, hardcoded `aria-internal` fallback) |
| morning | `2e0e405` | Reaction handler — wrong-key fix + dead-branch removal (working) |
| morning | `1960b23` | RESPONSE STYLE rewrite + `ingest_tier_c.py` |
| morning | `5a370b8` | Constitution clause 13 (no `[CONFIRMED]` on uncited current events / propaganda / topic bleed) + relevance filter + `/admin/purge-signals` endpoint |
| morning | `38b07c2` | Tighter relevance filter (multi-word match + stopword expansion) |
| morning | `21a3baf` | Local proxy routes for /forget /purgecases /purge-signals /report |
| morning | `45c1a66` | Constitution clause 14 (NO FABRICATED VERIFIABLE FACTS — Modirum Gespi incident) |
| morning | `4bfb716` | Fast `extract_url` tool (replaces slow crawl for inline chat) |
| morning | `bdaff6d` | MEM0 personal-notebook auto-summariser + persistent volume mount in `fly.toml` |
| morning | `d98c516` | `/admin/audit-user-consistency` endpoint (phantom admin detection) |
| morning | `63e226c` | Block propaganda-tier sources at intel-ledger ingest boundary |
| morning | `cc581f5` | `corpus_ingest.py` `VALID_TIERS` extended to A/B/B+/C/C+/D/E (sync with registry) |
| morning | `781b402` | Markdown normaliser at WA send boundary (rewrites `**bold**` → `*bold*`) |
| afternoon | (ops) | fly.io memory bumped 1024 → 2048 MB to prevent embed-loop OOM |
| afternoon | (ops) | All tier ingests run into the persistent volume — chromadb at 2,642 documents |

### 2026-04-09 (Phase 1 — shipped this session)

| Change | File | What | Why | How to verify |
|---|---|---|---|---|
| `ghost_detection_principles.py` | NEW `aria_service/intel/ghost_detection_principles.py` | Conditional Tier-D addendum: 10-point ghost entity checklist + structured DD output format. Fires on `/investigate`, "screen this counterparty", "are they legit", "ubo / beneficial owner", "shell company" intents. | Adds defences against counterparty fraud — a class of risk the existing constitution clauses don't directly address. Distilled from Antonio's six-pillar architecture proposal + the Omar J Jones / Modirum Gespi incident history. | Send a DD query like `Aria, screen this counterparty for me: <name>` — expect a structured reply with the 10-point checklist (each marked ✓/✗/?), explicit gaps section, sanctions screen breakdown, and BOTTOM-LINE GO/NO-GO/INVESTIGATE verdict. NO fabricated registry data. |
| `contract_review_principles.py` | NEW `aria_service/intel/contract_review_principles.py` | Conditional Tier-D addendum: 14-point mandatory contract checklist + 8 red-flag triggers + omission analysis + subtext lens + structured contract-review output format. Fires when (a) document attached AND (b) review verb + contract object both present in the query. | Adds defences against missing-clause traps in real deals — a class of risk currently uncovered. Closes a real Arkmurus workflow gap. | Attach a real contract PDF in WhatsApp + send `Aria, please review this NDA / contract / agreement` → expect a structured 14-point review with verbatim quotes, no fabricated clauses, omission analysis, and BOTTOM-LINE PROCEED/RENEGOTIATE/REJECT verdict. |
| Clause 12 extension | `aria_service/aria_engine.py` ARIA_SYSTEM_PROMPT | Added an OMISSION ANALYSIS sentence to constitution clause 12. Forbids filling in "standard contract language" where the document is silent — silence is a finding. | Strengthens the existing document-review clause against passive omissions (e.g. missing FCPA warranty, missing termination triggers, missing IP survival). | Ask ARIA to review a contract that's missing a standard clause she'd expect (e.g. no liability cap) — expect her to flag the omission explicitly, not invent one. |
| `negotiation_principles.py` DO NOTHING rule | `aria_service/intel/negotiation_principles.py` | Added principle 12: every recommendation involving a deal/approach/partnership/commitment MUST present DO NOTHING as a named option alongside active options. | Strategic discipline. Some queries don't have an active right answer — declining or waiting is a valid (sometimes best) move. The previous addendum implicitly assumed an active recommendation would always be appropriate. | Send a negotiation query like `Aria, how should I approach this counterparty?` — expect Option A / Option B / Option C — DO NOTHING in the structured reply, with explicit rationale for the DO NOTHING branch. |

**Changes to existing addenda layer:**
- Added: 2 new conditional addenda (`ghost_detection_principles`, `contract_review_principles`) — both behind their own env vars and intent detectors
- Extended: 2 existing modules (`negotiation_principles` adds principle 12; constitution clause 12 adds omission-analysis sentence)
- Removed: nothing — purely additive

**Token budget impact:**
- Always-on prompt: +0 chars (the new addenda are conditional)
- Counterparty DD queries: +~3,000 chars (ghost detection block injected)
- Contract review queries: +~4,000 chars (contract review block injected) — only when document is also attached
- Negotiation queries: +~700 chars (DO NOTHING principle adds to the existing block)

**Layer scorecard impact (delta from end of session 2):**
| Layer | Was | Now | Delta |
|---|---|---|---|
| TIERS | 85% | 85% | — |
| WEB SEARCH | 75% | 75% | — |
| AUTONOMOUS RESEARCH | 40% | 40% | — |
| MEM0 | 70% | 70% | — |
| OUTCOME TRACKING | 80% | 82% | +2 (contract review checklist gives the feedback layer more concrete behavioural targets to score against) |
| **NEW LAYER: COUNTERPARTY DD** | n/a | **65%** | new |
| **NEW LAYER: CONTRACT REVIEW** | n/a | **60%** | new |

**Aggregate readiness:** ~70% → ~72% (the additions are domain-specific so the aggregate moves modestly, but two new high-value workflows are now first-class).

**What this still doesn't address:**
- JS-rendered SPA crawling (extract_url falls back to meta tags only)
- Internal-IP 401 issue (background callers still missing bearer token)
- Tier B sources that 403'd (NATO DIANA, FATF, gov.uk etc.) — need a per-source UA / cookie strategy
- AUTONOMOUS RESEARCH still gated off behind `ARIA_AUTONOMOUS_RESEARCH_ENABLED=0`

---

## How to read this scorecard going forward

**Each new entry should answer:**
1. **What changed** — concrete files / lines / functions touched
2. **Why** — what failure mode or capability gap drove the change
3. **How to verify** — specific behavioural test that would prove the change works
4. **Layer impact** — which mental-module layer(s) moved up or down, by how much

**The aggregate score should move when:**
- A failure mode is closed (move up)
- A new failure surfaces from production (move down until fixed)
- A layer expands its data / capability (move up)
- A layer is disabled or regresses (move down)

We're not optimising for a number — we're using the number to make the work measurable. A trend of "70% → 80% → 90%" over a few weeks tells the story better than any single commit message.
