# ARIA 380-Degree Ecosystem Deep Dive — 2026-06-08

**Build rev**: 7dc04657 (live, matches origin/main)
**Audit method**: Automated ecosystem scan + per-module wiring grep + live health probe + agent registry/contract/vault inspection + north star Phase A gate mapping

---

## 1. ECOSYSTEM OVERVIEW

| Metric | Value |
|--------|-------|
| Total Python modules | 444 |
| Total functions | 5,012 (2,561 async, 2,451 sync) |
| Test files | 596 |
| Total tests | 6,057 |
| Syntax errors | 0 |
| Bug patterns (bare except:pass, wildcard imports) | 0 |
| Cross-reference issues (calls to non-existent functions) | 0 |
| Dead modules | 1 (`intel/auto/test_rf1191_new.py` — test artifact) |
| Routes (FastAPI endpoints) | 599 |
| Autonomous tasks (YAML-defined) | 54 |
| Environment flags (ARIA_*) | 270 |
| Brain-wired modules | 386/444 (87%) |
| Test-covered modules | 379/444 (85%) |

---

## 2. AGENT ECOSYSTEM — REGISTRATION & CONTRACTS

### 2.1 Registered Agents (at startup in main.py)

| Agent ID | Type | Task | Contract Registered? |
|----------|------|------|---------------------|
| research_engine | autonomous_research | RSS feeds → fact extraction → hypothesis validation (30min) | ❌ No contract passed |
| self_improve | autonomous_self_improve | Error-ledger analysis → bug detection → auto-fix (2h) | ❌ No contract passed |
| student_quiz | student_brain | Self-quiz on weak topics, mastery tracking (3h) | ❌ No contract passed |
| student_reading | student_brain | Study articles on weak topics (6h) | ❌ No contract passed |
| library_consolidation | student_brain | Archive stale reasoning cases (daily) | ❌ No contract passed |
| proactive_watch | proactive_engine | Daily briefing trigger + mastery prep (hourly) | ❌ No contract passed |
| weekly_report | reporting_engine | Weekly learning report (Monday 06-08 UTC) | ❌ No contract passed |
| watchlist_rescreen | dd_engine | Re-screen DD watchlist entities (daily) | ❌ No contract passed |
| tender_monitor | procurement_engine | Crawl defence procurement portals (6h) | ❌ No contract passed |
| web_integrity | monitoring | 24/7 endpoint monitoring | ❌ No contract passed |
| self_healing | infrastructure | Health checks, circuit breakers, auto-recovery | ❌ No contract passed |

**CRITICAL GAP**: All 11 agents register in the agent registry but NONE pass an `AgentContract`. The `_register_agent()` wrapper in `main.py:1065` only accepts `(agent_id, agent_type, task)` — it does not accept or pass a `contract` parameter. The `AgentRegistry.register()` method supports contracts (line 106) but they are never supplied from the boot path. This means:
- No agent has a binding contract with directives, inputs, outputs, error modes, or dependencies
- `CONTRACT_REGISTRY.validate_contract()` will always return violations for every agent
- Cross-agent dependency checking is non-functional
- The self-healing system cannot detect contract violations

### 2.2 Agent Registry Wiring

| Component | Wired? | Notes |
|-----------|--------|-------|
| AgentRegistry.register() | ✅ | Wires success/failure to brain (R-F1166) |
| AgentRegistry.tick_heartbeat() | ✅ | Wires success/failure |
| AgentRegistry.claim_gap() | ✅ | Wires success/failure |
| AgentRegistry.send_message() | ✅ | Wires success/failure |
| AgentContract.register_contract() | ✅ | Wires success/failure |
| AgentSignupVault.record() | ✅ | Wires to brain via notify_agents_about_vault |
| AgentSignupVault.update_status() | ✅ | Wires to brain |

### 2.3 Agent Signup Vault

| Metric | Value |
|--------|-------|
| Total signups | 28 |
| Status: pending | 25 (89%) |
| Status: registered | 2 (7%) |
| Status: verified | 1 (4%) |
| Real (non-test) signups | 0 |

**CRITICAL GAP**: All 28 entries are test artifacts. Zero real portal signups have been recorded. The vault exists and is wired, but no agent has actually used it to register a real signup. The `portal_registry` module needs to be integrated with the vault so that when an agent signs up to a portal, the vault records it.

---

## 3. BRAIN WIRING COMPLETENESS

### 3.1 Wired Modules (386/444 = 87%)

All core intel modules, autonomous modules, routes, and critical infrastructure are wired.

### 3.2 Dark Modules — Critical (need wiring)

| Module | Why It Matters |
|--------|---------------|
| `aria_engine.py` | The core chat engine — every user message flows through this. No brain signal on success/failure. |
| `intel/self_introspect_guard.py` | Self-introspection guard — critical for honesty. No wiring at all. |
| `intel/redis_store.py` | Redis persistence layer — failures here are invisible to the brain. |
| `intel/error_log_handler.py` | Error logging handler — errors here are invisible. |
| `intel/memory_wal.py` | Write-ahead log for memory persistence. |
| `llm/factory.py` | LLM provider factory — provider selection failures are dark. |
| `llm/provider.py` | LLM provider base class. |
| `llm/gemini.py` | Gemini provider — fallback path is dark. |
| `llm/hybrid.py` | Hybrid provider — fallback path is dark. |
| `llm/local_llm.py` | Local LLM provider — sovereign path is dark. |
| `llm/metered.py` | Metered LLM wrapper — cost tracking failures are dark. |
| `llm/prompt_budget.py` | Prompt budget management — budget violations are dark. |
| `search_engine/internal_search.py` | Internal search engine — search failures are dark. |
| `search_index/db.py` | Search index database — index failures are dark. |
| `search_index/indexer.py` | Search indexer — indexing failures are dark. |
| `writers/_resilient_llm.py` | Resilient LLM writer — write failures are dark. |

### 3.3 Dark Modules — Acceptable (CLI tools, crawlers, legacy)

| Category | Count | Examples |
|----------|-------|---------|
| CLI tools | 4 | `cli/ingest_corpus.py`, `cli/ingest_hardware_facts.py`, etc. |
| Crawlers | 4 | `crawler/fetcher.py`, `crawler/on_demand.py`, etc. |
| Integrations | 2 | `airtable_buffer.py`, `airtable_pipeline.py` |
| Multi-lang reviewers | 7 | `docker_reviewer.py`, `go_reviewer.py`, `rust_reviewer.py`, etc. |
| Scrapers | 4 | `generic_adapter.py`, `orchestrator.py`, `playwright_engine.py`, etc. |
| Sources | 11 | `acled.py`, `ofac_sdn.py`, `un_sc_sanctions.py`, etc. |
| Static/CLI client | 2 | `aria.py`, `aria_tui.py` |
| Other | 4 | `config.py`, `git_utils.py`, `sanctions_canonical/normalise.py`, `auto/test_rf1191_new.py` |

### 3.4 P1 Items — Honesty Foundation Wiring

| Item | Status | Evidence |
|------|--------|----------|
| P1-1: Dead /api/brain/signal callers | ✅ FIXED | All callers use /api/aria/brain/signal (R-F887/R-F900) |
| P1-2: Sweep sources dark + pushSignalsToBrain dead | ⚠️ IMPROVED | errorTracker.mjs has brain hook; pushSignalsToBrain still calls dead redisPush |
| P1-3: /channel/ingest silently drops failures | ❌ NOT FIXED | routes/aria.py:16211 returns {ok:False} with no WARNING+ or brain signal |
| P1-4: Honesty guards dark | ❌ NOT FIXED | premise_verifier.py, honesty_judge.py, self_claim_guard.py — 0 wire_success/wire_failure tokens |
| P1-5: semantic_search.py dark | ❌ NOT FIXED | 0 wire_success/wire_failure tokens on encode failure/timeout |
| P1-6: RUN-EVAL-DAILY disabled | ✅ FIXED | R-F929 re-enabled; tasks.yaml:1133 shows enabled:true |
| P1-7: UI shows Node store, not brain | ❌ NOT FIXED | Landing dashboard.html shows Node OSINT sweep, not brain state |
| P1-8: Adversarial staleness | ⚠️ IMPROVED | ADVERSARIAL-AUDIT task enabled (Wed+Sun); last score 0.239 (263h stale) |

---

## 4. PHASE A EXIT GATES — NORTH STAR COMPLIANCE

### Gate 1: Composite score ≥ 71% sustained
**Status**: ✅ CLOSED (R-F748, 2026-05-20: 0.668→0.723)

### Gate 2: Heatmap weakest cell ≥ 70%
**Status**: ✅ CLOSED (R-F748, 2026-05-20: 0.668→0.723)

### Gate 3: 0 fly ERROR logs in last 7 days
**Status**: ⏳ IMPROVED but NOT CLOSED
- R-F1381 (boot grace window + sustained-failure re-probe) shipped but NOT deployed (7dc04657 does not include it)
- The P0 wedge (state_store _conn=None reconnect race) is still causing live outages
- Gate 3 clock restarted ~22:30 2026-06-06 on 381919d3; 7dc04657 is live but the wedge is unfixed

### Gate 4: All quarantined DDs investigated + closed
**Status**: ✅ CLOSED

### Gate 5: All operator-pending env vars set
**Status**: ❌ NOT CLOSED
- `ACLED_EMAIL` + `ACLED_PASSWORD` — NOT set on fly (operator-pending)
- `ARIA_LLM_URL` — NOT set (sovereign LLM path dormant)
- `ARIA_RUNPOD_POD_ID` — NOT set (scheduler can't find pod)
- `ARIA_RUNPOD_START_HOUR` — NOT set

### Gate 6: 500-question evaluation set v1 frozen
**Status**: ✅ CLOSED (R-F1379, 2026-06-07: 500/500 entries, 52 categories)

### Gate 7: ≥ 4 design partner relationship conversations underway
**Status**: ❌ NOT CLOSED
- 4 draft emails exist in `data/design_partner_drafts.md`
- None have been sent — operator action needed

---

## 5. LIVE HEALTH

| Check | Status | Detail |
|-------|--------|--------|
| /health/live | ✅ alive | build_rev=7dc04657 |
| /health | ✅ operational | LLM=deepseek, state=sqlite/green |
| Autonomous loop | ✅ running | Level 3, dry_run=false, 96 tasks loaded |
| Diagnostic overall | ⚠️ AMBER | 71 pass, 4 warn, 0 fail |
| Circuit breakers | ⚠️ 3 open | rss, duckduckgo, archive_is |
| Adversarial score | ⚠️ 0.239 (263h stale) | Last run 2026-05-27 |
| Mastery overall | 0.509 | |
| Grounded rate | 0.786 | |

---

## 6. AGENT PLAYBOOK COMPLIANCE

### 6.1 What AGENTS.md Requires

The ARIA Coder playbook (AGENTS.md) mandates:
1. **Wire first, logic second** — wire_success() and wire_failure() before business logic
2. **Both branches** — success AND failure must reach a brain sink
3. **Capability tests** — every fix must drive the real broken path
4. **Verify-after-fix** — two passes (map → fix → verify → patch → re-verify)
5. **Self-critique** — adversarial attack on own work before sign-off

### 6.2 Compliance Assessment

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Wire first, logic second | ✅ 87% compliance | 386/444 modules wired; 58 dark (mostly acceptable) |
| Both branches wired | ⚠️ Partial | Many modules wire failure but not success (e.g., premise_verifier, self_claim_guard) |
| Capability tests per fix | ✅ | All recent R-numbers include capability tests |
| Verify-after-fix (2 passes) | ✅ | Enforced by CLAUDE.md §3 |
| Self-critique before sign-off | ✅ | R-F1123 enforced |
| Agent contracts at registration | ❌ BROKEN | No contracts passed from main.py boot path |
| Agent signup vault populated | ❌ BROKEN | 28 entries, all test artifacts |
| Kill switch halts all loops | ❌ BROKEN | Only engine loop checks pause flag (R-F1391) |

### 6.3 Agent-Specific Wiring Gaps

| Agent Module | wire_success | wire_failure | brain_hook | capability_gaps | mistake_ledger |
|-------------|-------------|-------------|-----------|----------------|---------------|
| self_healing.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| web_integrity_agent.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| self_introspect_guard.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| premise_verifier.py | ❌ | ❌ | ❌ | ✅ (capability_gaps) | ❌ |
| honesty_judge.py | ❌ | ❌ | ❌ | ✅ | ❌ |
| self_claim_guard.py | ❌ | ❌ | ❌ | ✅ | ❌ |
| semantic_search.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| aria_peers.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| brain_signal_consumer.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| continuous_learner.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| compliance_workflow.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| contact_intelligence.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| entity_graph.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| financial_dd.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| fatf_typologies.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| economic_substance.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| crypto_sanctions.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| dd_case_library.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| dd_disciplines.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| document_corrections.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| forensic_benford.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| github_search.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| global_defence_knowledge.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| gtm_strategy.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| international_law.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| kaspersky_mitigation.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| known_publisher_router.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| due_diligence_playbooks.py | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## 7. CRITICAL GAPS (must fix before full autonomy)

### P0: State store wedge (live outage class)
- **What**: `state_store._conn=None` reconnect race causes silent write drops + AttributeError crashes
- **Evidence**: Live wedge at 10:15 2026-06-07 — aiosqlite contention → _conn=None → UPSERT fails → doc job lost
- **Fix approved**: Atomic reconnect swap (fix B) + nuanced critical fail-loud (fix A) + json.dump-on-loop check
- **Claude's instruction**: Read `/data/wedge_stacks/wedge_674_1780824803.log` on live server to identify actual frames
- **Status**: ❌ NOT STARTED

### A1: Kill switch doesn't halt all loops
- **What**: `POST /api/aria/autonomous/pause` only stops engine task scheduler. Coder loop, gap_detector loop, and self-improve loop are NOT stopped.
- **Evidence**: Capability test written at `test_rf1391_kill_switch_pause_all_loops.py` — all 3 loops fail the pause check
- **Status**: ❌ TEST WRITTEN, NOT FIXED — launch-blocker per Claude

### Agent contracts not registered at boot
- **What**: `_register_agent()` in main.py does not accept or pass `contract` parameter. Zero contracts registered at startup.
- **Impact**: No agent has binding directives, no cross-agent dependency checking, no contract violation detection
- **Status**: ❌ NOT FIXED

### Agent signup vault empty (real data)
- **What**: All 28 vault entries are test artifacts. Zero real portal signups recorded.
- **Impact**: The vault is the single source of truth for "what have our agents signed up to?" — it's useless today
- **Status**: ❌ NOT FIXED

### 27 intel modules completely unwired
- **What**: 27 intel modules (DD layers, sanctions, compliance, entity graph, etc.) have zero brain wiring tokens
- **Impact**: Failures in these modules are invisible to the brain. The operator cannot see when a DD layer fails.
- **Status**: ❌ NOT FIXED

### P1-3: /channel/ingest silently drops failures
- **What**: routes/aria.py:16211 returns {ok:False} with no WARNING+ log and no brain signal on intel_ledger.add_signal failure
- **Status**: ❌ NOT FIXED

### P1-4: Honesty guards partially unwired
- **What**: premise_verifier.py, honesty_judge.py, self_claim_guard.py have 0 wire_success/wire_failure tokens. They record to capability_gaps on failure but do NOT wire success to the brain.
- **Status**: ❌ NOT FIXED (success branch unwired)

### P1-5: semantic_search.py dark
- **What**: 0 wiring tokens on encode failure/timeout. The wedge-central encoder is invisible to the brain.
- **Status**: ❌ NOT FIXED

### P1-7: UI shows Node store, not brain
- **What**: Landing dashboard.html shows Node OSINT sweep (VIX/Brent/signals). No brain-state strip.
- **Status**: ❌ NOT FIXED

### E1a: No fresh reasoning baseline
- **What**: The 500-Q eval on live DeepSeek shows 21.6% pass rate (98/453) but this is stale and likely mis-measured (cosine scorer may mark correct answers as failures)
- **Status**: ❌ NEEDS FRESH RUN + 10-SAMPLE SPOT-CHECK

---

## 8. WHAT'S WORKING WELL

### 8.1 Agent Infrastructure
- AgentRegistry: ✅ Full CRUD, heartbeat, gap claiming, inter-agent messaging, brain wiring
- AgentContract: ✅ Full CRUD, validation, violation tracking, dependency checking (tested, just not wired at boot)
- AgentSignupVault: ✅ Full CRUD, status tracking, brain notification (just empty)
- CapabilityGaps: ✅ Deduplication, 500-entry cap, 20+ gap types, brain wiring
- MistakeLedger: ✅ Immutable records, similarity lookup, prevention tracking, chain verification

### 8.2 Autonomous Engine
- ✅ 54 tasks defined in YAML
- ✅ 11 agents registered at startup with heartbeats
- ✅ All agents wire success/failure to brain via _wire_agent_success/_wire_agent_failure
- ✅ Cost tracking, rate limiting, circuit breakers all active
- ✅ DeepSeek pinned as coder + reviewer (R-F1366)
- ✅ All 8 historical coder failure modes structurally guarded

### 8.3 Phase A Progress
- ✅ Gate 1 (composite ≥71%): CLOSED
- ✅ Gate 2 (heatmap floor ≥70%): CLOSED
- ✅ Gate 4 (quarantined DDs): CLOSED
- ✅ Gate 6 (500-Q eval set): CLOSED
- ✅ R-F1384 wrong-answer P0: PROVEN LIVE on operator's real NDA test

---

## 9. RECOMMENDED PRIORITY ORDER

Based on north star compliance + live reliability + agent playbook readiness:

1. **P0: State store wedge fix** — atomic reconnect swap + critical fail-loud + read wedge stack file. This is causing LIVE outages.
2. **A1: Kill switch fix** — add pause checks to coder/gap_detector/self-improve loops. Launch-blocker per Claude.
3. **Agent contracts at boot** — add contract parameter to `_register_agent()` and define contracts for all 11 agents.
4. **Wire 27 intel modules** — add wire_success/wire_failure to all unwired intel modules (DD layers, sanctions, compliance).
5. **P1-4: Wire honesty guard success branches** — add wire_success to premise_verifier, honesty_judge, self_claim_guard.
6. **P1-3: Fix /channel/ingest silent drop** — add WARNING+ log + brain signal on failure.
7. **P1-5: Wire semantic_search.py** — add brain wiring on encode failure/timeout.
8. **E1a: Fresh 500-Q eval** — run eval_runner against live DeepSeek + 10-sample spot-check.
9. **Populate agent signup vault** — integrate portal_registry with vault for real signups.
10. **P1-7: Fix UI brain state** — add brain-state strip to dashboard.
11. **Gate 5: Set ACLED creds** — operator action to set fly secrets.
12. **Gate 7: Send design partner emails** — operator action to send 4 draft emails.

---

## 10. SUMMARY

ARIA's agent ecosystem is **architecturally sound but operationally incomplete**. The core infrastructure (registry, contracts, vault, gaps, ledger) is built, tested, and wired. But:

- **Agents register without contracts** — the contract system exists but is never populated at boot
- **The signup vault is empty** — the single source of truth for agent portal access has zero real data
- **27 intel modules are dark** — failures in DD layers, sanctions, and compliance are invisible to the brain
- **The kill switch doesn't kill** — the operator's emergency stop only halts one of four loops
- **The state store wedge is still live** — the recurring P0 that causes doc job loss is unfixed

The north star Phase A exit gates are **4 of 7 closed**. Gates 3 (0 ERRORs/7d), 5 (env vars), and 7 (design partners) remain open. The gate 3 clock cannot start cleanly until the P0 wedge is fixed.

**Verdict**: The ecosystem is ready for supervised autonomy (current state) but NOT for full autonomous operation. Three launch-blockers remain: (1) P0 wedge fix, (2) A1 kill switch fix, (3) agent contracts at boot. Fix these three and the agent ecosystem is structurally complete for Phase A exit.
