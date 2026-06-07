# Claude → ARIA: full 360 DD note (2026-06-07, verified against live build 3cb6965a)

Operator has asked you for a FULL DD. This is my complete audit state so yours starts where mine ended — verify, don't trust; everything below carries the evidence type used (live probe / test run / file:line read). Your last DD (`ecosystem_audit_results.json`) was a census that found zero behavioral bugs — the standard for this one is ADVERSARIAL: every claim probed, every gate endpoint read for what it actually measures, every "fixed" re-run.

## 0. Method standard (binding for this DD)
1. RUN probes, don't count modules: live curls expecting 401s, red tests, function-level runs (`_tokenize_entity_name('Сбербанк')` class), gate-endpoint cross-checks.
2. Never mark a gate CLOSED from an endpoint without reading what the endpoint measures (gate-7 counted chat rows).
3. A DD that finds zero bugs is presumed broken.
4. Cite file:line or paste the probe output for every verdict. UNVERIFIED where you can't. Distinguish "code exists" / "registered at boot" / "live".
5. Live state beats memory and beats docs — CLAUDE.md §17 AUTO_DEPLOY claim was stale; the secret was 0.

## 1. CLEARED — verified fixed live, do NOT re-flag (re-probe only to confirm still true)
- `/token`, `/api/aria/learning/updates`, `/api/aria/client/chat`, Zoom webhook no-sig → all 401 live (R-F1347/F1349). Token ROTATED (leaked 8b8eca… dead).
- UBO walk NameError fixed (network_walker.py:789 + nato_standards.py:1012). R-F1348 sanctions screen-gap surfacing in.
- Kill-switch R-F1395 LIVE — all 12 loops check is_engine_paused(); flag round-trip live-proven; behavioral drill in progress today.
- state_store reconnect race CLOSED (R-F1397: probe-before-churn + new-conn-first swap, no _conn=None window) — Issue 1 done.
- Tesseract OCR off the event loop (R-F1398 to_thread) — Issue 2 done. KNOWN FOLLOW-UP: fitz decode in pdf_deep_ingest still on-loop (same class, smaller share) — recorded as coder gap; verify it's in your gap store.
- WA wrong-document P0 + false "job expired" + unretried 503 (R-F1391/92/93) live.
- Judge eval scorer live (R-F1396) — RUN-EVAL-DAILY summaries now carry judge_coverage + scorer.
- predictor_gate composite inflation removed (R-F1350). Auth core, report signing fail-closed, §13 stream parity, date/TZ hygiene, no secrets in repo — all verified sound 06-05/06-07.

## 2. OPEN — confirmed bugs, fix-or-gap each one (§21e: if the coder can express it as a Gap, record it)
**Compliance P0s:**
- Sanctions nasab FN — `'Osama bin Laden'` properly cased → `_looks_like_entity_name` False (sanctions.py:742; bin/al not name-particles). Live-run verified.
- Non-Latin FN — `_tokenize_entity_name('محمد عبدالله')→set()`, CJK too → screens clean (_sanctions_classify.py). Live-run verified.
**Safety/cost:**
- $50/day autonomous cap INERT — record_task_cost only fires in timeout branches (tasks.py:1587/1666); success path never charges. Live daily_spent was 0.0 with 96 tasks (note: probe today showed 2.0 — re-check what now writes it).
- Monthly $300 rollup non-atomic RMW (cost_tracker.py:458-502) under the silent-drop class; atomic incrbyfloat (state_store) exists unused there.
**Gate honesty (you confirmed all 5):**
- phase_gates_ep: gate-7 counts chat_audit rows (routes/aria.py:20931); gate-5 wrong env names + omits ACLED (:20899; NOTE: ACLED now DEFERRED by operator until MVP — reflect that, don't list as blocker); gate-6 reads unset redis key `crucix:aria:eval:500q:status`; gates 1-3 "unknown" while dedicated endpoints answer (two sources of truth).
- source_verifier.py:353-380 auto-grounds any tool/doc turn at 1.0 (45% composite weight). Gameable.
- Heatmap floor real value ~0.507 vs 0.70 (gate #2 genuinely open — curriculum work, not endpoint work).
**Alert delivery (operator-blind class):**
- dedup.mjs:181 marks signals seen at FILTER time; markSignalsSent dead (0 callers) → failed Telegram/SMTP send = alert dropped forever.
- Telegram/email/digest send failures console-only (DARK, §21b). server.mjs:5180 sweep-executor failure not brain-wired.
- WA media (doc/img/voice) bypasses _isDuplicateMessage (runs before dedup at :1587) → dup processing on reconnect; dedup in-memory + second-granularity.
**Playbook truth (docs/ARIA_TEAM_PLAYBOOK.md over-promises):**
- Email inbox 5-min poll DEAD: `email_reader.start_background_polling()` (email_reader.py:539) never called at boot; Node emailReader default-off → LinkedIn channel dead too.
- MISSING: Wed-9am compliance PDF, monthly battlecards/network-gap report, 3am memory consolidation. Sanctions refresh daily not 4h. `/pipeline` `/deal` in /help with no handlers.
- memory-WAL drain loop DARK (main.py:1388 logs only).
**Web/security P1-P2:**
- No CSP/helmet on Node tier + raw innerHTML aria-brain.html:1089 (stored-XSS surface).
- `/api/admin/env-check` 200 unauth (env-presence map + pid; no token since R-F1286) — re-gate.
**Coder trust:**
- Staged queue 48 entries with shrink/mass-rewrite proposals (safety.py 275 vs 545 lines; circuit_breaker 197 vs 218; intel/__init__ 2→115) — must be graded (E2a/E2b) before AUTO_DEPLOY ever flips. AUTO_DEPLOY currently 0 = correct.

## 3. UNHUNTED GROUND (from 06-05, still never deep-read — pick targets for your full DD)
VLS-chain UI + model-card/status pages · intel-module ALGORITHM correctness (dd_disciplines, forensic_benford, fatf_typologies, entity_resolver, economic_substance) · build/Docker supply-chain (pinned deps, Lightpanda fetch, image provenance) · eval HARNESS gameability depth · VLS/ECDSA key-rotation story · Companies House/GLEIF/OpenSanctions adapter correctness (rate-limit, parse, stale-cache) · Telegram alert formatting failure paths · rate_limiter under spoofable session_id · WA per-chat state concurrency (_recentDocs/history interleaving) · full-suite re-run (baseline stale: 91-fail/5958, test_rf1230 wedge).

## 4. Deliverable format
One report file with: per-finding evidence type (probe/test/read), severity, CLEARED-list re-confirmation table, new findings, and a Gap recorded for every coder-fixable item. End with the §19e deploy status of anything you ship. Date-stamp check: today is 2026-06-07 (your last report said 06-08).
