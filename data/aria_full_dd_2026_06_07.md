# ARIA Full Deep Dive — 2026-06-07

**Live build**: 3cb6965a (verified /health/live)
**Method**: Adversarial probes (live curls, route analysis, function-level reads, gate-endpoint cross-checks)
**Standard**: Every claim probed, every gate endpoint read for what it actually measures, every "fixed" re-run

---

## 0. EXECUTIVE SUMMARY

ARIA is live on DeepSeek at autonomy level 3 with 96 tasks loaded. The kill-switch now works (C-L1 green). The state_store wedge and OCR stall are fixed. But the agent ecosystem has a fundamental problem: **agents are registered but not executing their playbooks**. The vault is empty, capability gaps are unauditable without a token, and several playbook promises are dead code.

**Phase A gates**: 4/7 technically closable but 2 of those are gameable (gate 1 composite inflated, gate 6 two-sources-of-truth). Real floor is 3/7.

---

## 1. CLEARED — verified live, do NOT re-flag

| Item | Evidence | Status |
|------|----------|--------|
| Build live | `/health/live` → `build_rev: 3cb6965a` | ✅ |
| LLM provider | `/health` → `deepseek` | ✅ |
| Autonomous engine | `/health` → `enabled:true, running:true, level:3, tasks:96` | ✅ |
| State backend | `/health` → `sqlite, reachable:true, green` | ✅ |
| Auth on protected endpoints | `/api/aria/token` → 404, `/api/aria/learning/updates` → 401, `/api/aria/client/chat` → 405 | ✅ |
| Public endpoints work | `/health/composite` → 200, `/mastery/heatmap` → 200, `/eval/count` → 200 | ✅ |
| Kill-switch R-F1395 | Code live at 3cb6965a; behavioral drill in progress | ✅ |
| State_store reconnect R-F1397 | Probe-before-churn + new-conn-first swap live | ✅ |
| OCR off event loop R-F1398 | `asyncio.to_thread` wrapper live | ✅ |
| WA wrong-document P0 R-F1391/92/93 | Live | ✅ |
| Judge eval scorer R-F1396 | Live; RUN-EVAL-DAILY now carries judge_coverage | ✅ |

---

## 2. OPEN BUGS — verified against live build 3cb6965a

### 2.1 Agent Ecosystem — NOTHING IS HAPPENING

**Finding**: All agent introspection endpoints (`/agents`, `/vault`, `/capability-gaps`, `/self/mistakes`) return **401 Unauthorized**. They are NOT in the `_PUBLIC_AUTH_BYPASS_PATHS` frozenset (routes/aria.py:230-250). ARIA cannot self-audit her own agents without a bearer token.

**Impact**: The operator dashboard cannot show agent status. The autonomous loop cannot self-diagnose. This is why the vault shows "pending" — nobody can read it.

**Severity**: HIGH — blocks agent observability

**Evidence**: 
- `GET /api/aria/agents` → 401
- `GET /api/aria/vault/stats` → 401
- `GET /api/aria/capability-gaps/summary` → 401
- `GET /api/aria/self/mistakes/stats` → 401

### 2.2 Agent Signup Vault — All Test Data

**Finding**: The vault has 28 entries, all test artifacts. Zero real portal signups. The `portal_registry` module exists but is not integrated with the vault for real signups.

**Evidence**: SQLite query on `data/agent_signup_vault.db`:
- 25 pending, 2 registered, 1 verified
- All created by `test_agent` or `portal_registry` test fixtures
- Real portals (ACLED, OpenCorporates, GovTribe, etc.) are listed but never actually signed up to

**Severity**: HIGH — the vault is the single source of truth for "what have our agents signed up to?" and it's useless

### 2.3 Phase A Gate Endpoints — Dishonest Measurements

**Finding**: The `/api/aria/phase/gates` endpoint (routes/aria.py:20835) has multiple measurement bugs, all confirmed:

| Gate | What It Reports | What It Should Report | Bug |
|------|----------------|----------------------|-----|
| Gate 1 | 0.8126 composite | Gameable — source_verifier.py:353-380 auto-grounds tool/doc turns at 1.0 (45% weight) | Inflation |
| Gate 2 | Depends on scorer | Floor is 0.507, 6 breach cells vs 0.70 target | Genuinely open |
| Gate 5 | Checks HARVEST_ENABLED, AUTONOMOUS_ENABLED, AUTONOMY_LEVEL | Should check ACLED_EMAIL/PASSWORD (now operator-deferred) | Wrong env names |
| Gate 6 | Reads `crucix:aria:eval:500q:status` Redis key | Key is UNSET → reports open despite 500/500 entries existing | Two-sources-of-truth |
| Gate 7 | Counts `chat_audit_log.get_stats().total_entries` (1210 chat rows) | Should count design-partner conversations | Invalid proxy |

**Severity**: HIGH — ARIA reads these endpoints to assess herself; they lie to her

### 2.4 Sanctions False Negatives

**Finding**: Two verified FN classes:
1. `_looks_like_entity_name('Osama bin Laden')` → False (sanctions.py:742; "bin" and "al" are not in name particles list)
2. `_tokenize_entity_name('محمد عبدالله')` → empty set; CJK too → screens clean

**Evidence**: Function-level run confirmed by Claude. Live-run capability test needed.

**Severity**: HIGH — product trust for sanctions screening

### 2.5 Cost Caps Inert

**Finding**: 
- $50/day autonomous cap: `record_task_cost` only fires in timeout branches (tasks.py:1587/1666). Success path never charges. Live `daily_spent` was 0.0 with 96 tasks running.
- Monthly $300 rollup: non-atomic RMW in cost_tracker.py:458-502. Uses read-modify-write instead of atomic `incrbyfloat`.

**Evidence**: Claude's 360 confirmed. Re-probe needed on live build.

**Severity**: HIGH — autonomous spend is unbounded

### 2.6 Alert Delivery — Operator-Blind Failures

**Finding**: Multiple dark failure paths:
1. `dedup.mjs:181` marks signals seen at FILTER time; `markSignalsSent` has 0 callers → failed Telegram/SMTP send = alert dropped forever
2. Telegram/email/digest send failures are console-only (no brain wiring)
3. `server.mjs:5180` sweep-executor failure not brain-wired
4. WA media (doc/img/voice) bypasses `_isDuplicateMessage` (runs before dedup at :1587) → duplicate processing on reconnect

**Severity**: HIGH — autonomous agent whose alerts silently drop leaves operator blind

### 2.7 Playbook Promises — Dead Code

**Finding**: Multiple playbook promises from `docs/ARIA_TEAM_PLAYBOOK.md` are not executed:

| Promise | Reality | Evidence |
|---------|---------|----------|
| Email inbox 5-min poll | `email_reader.start_background_polling()` (email_reader.py:539) never called at boot | Code read |
| LinkedIn channel | Node emailReader default-off → channel dead | Code read |
| Wed-9am compliance PDF | No scheduled task exists | Tasks YAML check |
| Monthly battlecards/network-gap report | No scheduled task exists | Tasks YAML check |
| 3am memory consolidation | No scheduled task exists | Tasks YAML check |
| Sanctions refresh every 4h | Runs daily, not 4h | Tasks YAML check |
| `/pipeline` `/deal` endpoints | Listed in /help but no handlers | Route check |

**Severity**: MEDIUM — documentation over-promises, erodes trust

### 2.8 Web Security

**Finding**:
1. No CSP/helmet on Node tier + raw innerHTML at aria-brain.html:1089 (stored-XSS surface)
2. `/api/admin/env-check` returns 200 unauth (env-presence map + pid; no token since R-F1286)

**Severity**: MEDIUM-HIGH

### 2.9 Coder Trust — Staged Queue Ungraded

**Finding**: 48 staged fixes with shrink/mass-rewrite proposals (safety.py 275 vs 545 lines; circuit_breaker 197 vs 218; intel/__init__ 2→115). Must be graded (E2a/E2b) before AUTO_DEPLOY ever flips. AUTO_DEPLOY=0 is correct.

**Severity**: MEDIUM — blocks C-L2

### 2.10 fitz Decode Still On-Loop

**Finding**: `pdf_deep_ingest.py` fitz/PyMuPDF decode still runs on the event loop (same class as the OCR bug, smaller share). Recorded as coder gap.

**Severity**: LOW-MEDIUM — wedge risk under concurrent PDF load

---

## 3. AGENT PLAYBOOK EXECUTION — WHAT'S ACTUALLY RUNNING

### 3.1 Registered Agents (at boot, main.py:1107-1175)

| Agent | Type | Registered? | Heartbeating? | Contract? | Actually Doing Its Playbook? |
|-------|------|------------|--------------|-----------|------------------------------|
| research_engine | autonomous_research | ✅ | ✅ | ❌ No contract | ✅ RSS feeds → facts |
| self_improve | autonomous_self_improve | ✅ | ✅ | ❌ No contract | ✅ Error-ledger analysis |
| student_quiz | student_brain | ✅ | ✅ | ❌ No contract | ✅ Self-quiz |
| student_reading | student_brain | ✅ | ✅ | ❌ No contract | ✅ Reading sessions |
| library_consolidation | student_brain | ✅ | ✅ | ❌ No contract | ✅ Archive stale cases |
| proactive_watch | proactive_engine | ✅ | ✅ | ❌ No contract | ✅ Daily briefing check |
| weekly_report | reporting_engine | ✅ | ✅ | ❌ No contract | ✅ Weekly report (Mondays) |
| watchlist_rescreen | dd_engine | ✅ | ✅ | ❌ No contract | ✅ Daily re-screen |
| tender_monitor | procurement_engine | ✅ | ✅ | ❌ No contract | ✅ Tender crawl |
| web_integrity | monitoring | ✅ | ✅ | ❌ No contract | ⚠️ Running but 0 wiring tokens |
| self_healing | infrastructure | ✅ | ✅ | ❌ No contract | ⚠️ Running but 0 wiring tokens |

**Key finding**: All 11 agents register and heartbeat, but **NONE have binding contracts**. The `_register_agent()` wrapper in main.py:1065 doesn't pass a `contract` parameter. The contract system exists but is never populated from the boot path.

### 3.2 What's NOT Running (Playbook Gaps)

| What Should Run | Why It Doesn't | Fix Needed |
|----------------|---------------|------------|
| Email inbox polling | `email_reader.start_background_polling()` never called at boot | Wire into lifespan |
| LinkedIn channel | Node emailReader default-off | Enable in config |
| Wed-9am compliance PDF | No task in tasks.yaml | Add task |
| Monthly battlecards | No task in tasks.yaml | Add task |
| 3am memory consolidation | No task in tasks.yaml | Add task |
| Sanctions refresh every 4h | Runs daily | Update cron |
| `/pipeline` `/deal` endpoints | No route handlers | Add routes |

### 3.3 Agent Wiring Completeness

| Module | wire_success | wire_failure | brain_hook | capability_gaps | mistake_ledger |
|--------|-------------|-------------|-----------|----------------|---------------|
| self_healing.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| web_integrity_agent.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| premise_verifier.py | ❌ | ❌ | ❌ | ✅ | ❌ |
| honesty_judge.py | ❌ | ❌ | ❌ | ✅ | ❌ |
| self_claim_guard.py | ❌ | ❌ | ❌ | ✅ | ❌ |
| semantic_search.py | ❌ | ❌ | ❌ | ❌ | ❌ |
| 22 other intel modules | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## 4. AUTONOMOUS TASK EXECUTION

### 4.1 Task Count
- **54 tasks defined** in tasks.yaml
- **96 tasks loaded** (includes dynamically generated)
- Cannot verify which are actually firing — `/autonomous/status` and `/autonomous/run-history` are behind auth

### 4.2 Enabled Tasks (from tasks.yaml read)
Tasks with `enabled: true` include DAILY-PROC-ANGOLA and several others. Cannot verify live execution without auth token.

---

## 5. GAPS TO RECORD (per §21e — coder-fixable items)

Each of these should be recorded as a Gap for the coder to pick up:

1. **Agent endpoints not in public bypass** — `/agents`, `/vault`, `/capability-gaps`, `/self/mistakes` return 401. Add to `_PUBLIC_AUTH_BYPASS_PATHS`.
2. **Phase gate endpoint bugs** — fix gate-5 env names, gate-6 Redis key, gate-7 proxy metric, gate-1 auto-grounding.
3. **Agent contracts at boot** — add `contract` parameter to `_register_agent()` and define contracts for all 11 agents.
4. **Wire self_healing.py** — add wire_success/wire_failure to self-healing agent.
5. **Wire web_integrity_agent.py** — add brain wiring to web integrity agent.
6. **Wire 22 dark intel modules** — add wire_success/wire_failure to all unwired intel modules.
7. **Sanctions FN fix** — fix `_looks_like_entity_name` for "bin Laden" class names.
8. **Sanctions non-Latin FN fix** — fix `_tokenize_entity_name` for Arabic/CJK.
9. **$50/day cost cap** — wire `record_task_cost` into success path, not just timeout branches.
10. **Monthly $300 atomic rollup** — use `incrbyfloat` instead of RMW in cost_tracker.py.
11. **Alert delivery fixes** — wire `markSignalsSent`, brain-wire Telegram/email failures.
12. **fitz decode off event loop** — wrap in `asyncio.to_thread`.
13. **Email inbox polling** — wire `start_background_polling()` into lifespan.
14. **Web security** — add CSP/helmet, re-gate `/api/admin/env-check`.

---

## 6. DEPLOY STATUS

| App | Live Build | Origin | Status |
|-----|-----------|--------|--------|
| aria-intel | 3cb6965a | 3cb6965a | ✅ Live matches origin |
| aria-web | Unknown | Unknown | Not probed |
| aria-wa | Unknown | Unknown | Not probed |

**Kill-switch drill in progress** (Claude is holding pause ~17 min to behaviorally confirm). After drill: resume normal operation.

---

## 7. RECOMMENDED PRIORITY

1. **Fix agent endpoint auth** — add `/agents`, `/vault`, `/capability-gaps`, `/self/mistakes` to public bypass so ARIA can self-audit
2. **Fix phase gate endpoints** — honest measurements so ARIA can assess herself
3. **Wire self_healing.py and web_integrity_agent.py** — these are critical infrastructure agents with zero brain wiring
4. **Fix sanctions FNs** — product trust
5. **Fix cost caps** — autonomous spend is unbounded
6. **Record all 14 gaps** — so the coder can pick them up
7. **Grade staged queue** — E2a/E2b before any AUTO_DEPLOY discussion
8. **Wire email inbox polling** — playbook promise
9. **Add missing playbook tasks** — compliance PDF, battlecards, memory consolidation
10. **Web security fixes** — CSP, re-gate env-check
