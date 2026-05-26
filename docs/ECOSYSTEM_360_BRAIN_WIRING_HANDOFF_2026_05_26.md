# ARIA Ecosystem 360 — Brain-Wiring DD Assessment (2026-05-26)

**Author:** assessment session (Claude). **For:** operator + implementing/coding agent.
**Status:** STAGED scratch doc — not R-numbered. Reserve your own R-numbers per CLAUDE.md §2; verify-after-fix §3 (2 passes) + lifespan smoke §9 apply to every code change.
**Lens:** operator directive — *everything must be wired into ARIA's brain so she observes it and it feeds her autonomous problem-solving + learning loop.* "Wired" = emits `brain_hook.absorb` / `capability_gaps.record_gap` / `mistake_ledger.record` / a metric / a `/api/aria/brain/signal` POST on BOTH success and failure.
**Baseline:** `main` @ `96f10b0` (R-F888), aria-intel deployed v1037→1038 mid-assessment. All file:line accurate as of this commit — re-confirm before editing; line numbers drift.

Method: 6 parallel domain audits (autonomous core, intel/DD/compliance, API surface, Node+WA, web/UI, live production) + direct verification of the two highest-severity claims (gap_detector compile, logger namespace) + live Fly logs/endpoints.

---

## Headline

The 2026-05-25 "coder is structurally blind / 0 raw signals" P0 is **GENUINELY FIXED** (R-F884). Live proof: `[gap_detector] scan complete: 43 actionable gaps (from 48 raw signals)`. All 5 active extractors now read keys real producers write, and the two richest stores (`capability_gaps`, `mistake_ledger`) are finally read. Gate #3 passes live (`phase_a_gate_3_pass:true`, 7 clean days), gate #6 holds (eval 500), grounded_rate is healthy live at **0.958** (the prior 0.042 alarm was transient/fixed).

**But the loop now SEES gaps it cannot ACT on, and the other two tiers (Node web + WhatsApp) + most compliance/honesty engines are still dark.** The autonomous self-improvement loop is detecting 43 gaps/scan and safety-blocking every fix attempt (`rate_limit_exceeded`). Observability is still effectively Python-brain-only.

---

## What is genuinely WIRED (verified live — give credit, don't re-fix)

- **gap_detector reconnect (R-F884):** all 5 active extractors read real producer keys; dead-key extractors (`health:perf:latest`, `sweep:last_result`) removed; `capability_gaps` + `mistake_ledger` now read. Live: 48 raw → 43 actionable.
- **§13 stream-bypass parity:** chat_audit + brain_hook.absorb + 7-guard honesty chain all mirrored in BOTH `aria_chat` and `aria_chat_stream` (aria_engine.py:3508/4165, routes/aria.py:8094/8417). No bypass.
- **`/api/aria/brain/signal` route (R-F887 backend):** real, not a stub — routes failure-type signals → `capability_gaps.record_gap`, content → `brain_hook.absorb`, returns 202 (routes/aria.py:11250-11308). Works live (POST→401 auth-gated, route registered).
- **`/webhook/{source}`:** HMAC-verified → intel_ledger + brain_hook.absorb, both paths. Fully wired.
- **read-document / dd-orchestrate core failures:** escalate to `capability_gaps.record_gap` / `_log.exception` (ERROR) → reach brain.
- **self_diagnostic RED → gap pipeline:** confirmed it calls `brain_hook.absorb(gap_type=...)` on RED (self_diagnostic.py:855-871), not just the dashboard. (Coverage is the issue, not wiring — see P2.)
- **UI plumbing:** 0 dead/stub/404 buttons across 19 pages; auth (JWT + token-version revocation) and Stripe billing (signature-verified webhook, graceful unconfigured banners) sound.
- **Lifespan loops:** dominant pattern is `while True: try/except → logger.warning` → reaches error ledger; self-healing.

---

## P0 — must fix (brain blind to critical signals, or loop can't act)

### P0-1 — Autonomous loop detects 43 gaps but safety-blocks every fix (`rate_limit_exceeded`)
Live: every `fix_gap` returns `Safety guardrail: rate_limit_exceeded:27/28/29`. Root cause: `MAX_FIRINGS_PER_HOUR=12` (safety.py:60) and `check_and_increment_rate` (safety.py:418) **increments the hourly bucket on every attempt including blocked ones** — so with a 43-gap backlog the 12 slots exhaust and the counter climbs indefinitely, blocking the rest of the hour. The loop cannot drain its backlog.
- **Compounding:** gap_detector likely runs **twice** — `coder_entrypoint.py:215` spawns `gap_detector.run_forever()` AND the coder's `_one_cycle` (self_coder.py) calls `gap_detector.scan()` itself; live scan-complete log line is **doubled**. Double detection = double the firing-rate burn.
- **Also:** the coder picks up gaps in its OWN non-auto-deployable files (`routes/aria.py`, `self_coder.py` — both in R-F851 `NO_AUTODEPLOY_FILES`), so slots are spent on gaps that can only ever stage, not fix.
- **Fix:** (a) don't increment the rate bucket when the call is rate-blocked (only count *executed* firings); (b) de-duplicate gap_detector so it runs once, not in both the standalone loop and `_one_cycle`; (c) prioritise auto-fixable, auto-deployable gaps ahead of stage-only ones; (d) consider raising/tuning `ARIA_AUTONOMOUS_MAX_FIRINGS_PER_HOUR` once (a)/(b) land. Capability test: seed N>12 gaps, assert executed fixes == budget and counter doesn't run away.

### P0-2 — R-F886's compliance-layer WARNINGs never reach the brain (wrong logger namespace)
`dd_orchestrator.py:71` → `logging.getLogger("ARIA.DDOrchestrator")` (capital). `error_log_handler.py:127` filters `record.name.startswith("aria")` (lowercase) and `:170` attaches the handler only to `logging.getLogger("aria")`. `"ARIA.*"` is neither a child of `"aria"` nor matches the prefix → the 8 layer WARNING promotions R-F886 shipped 2 commits ago (PSC, weapon-catalogue, aggregator, typology, end-user, mou-gate, expert-block) are **invisible to the coder/brain**. R-F886 is effectively cosmetic for brain-wiring. *(A verify-after-fix §3 miss — the fix didn't achieve its stated goal.)*
- **Fix:** rename to `logging.getLogger("aria.dd_orchestrator")`, OR give each layer an explicit `self_improve.record_error`/`capability_gaps.record_gap`. Capability test: emit a layer WARNING, assert it lands in the error ledger.

### P0-3 — Eliminated-weapons watchlist is DARK; a banned-weapon screen miss is silent
`eliminated_weapons_watchlist.py` = 0 wiring tokens. Its only brain signal lives in the caller (`dd_orchestrator.py:2704-2715`, `record_error("compliance_engine_failure")`) but is gated by `_ewl_err_logged` (`:2656`) so **only the FIRST failed goods line per DD run** is recorded — a multi-line shipment with the screener broken reports one error and silently drops the rest. The `record_error` call is itself wrapped in `except: pass` (`:2714`). Surrounding screening engines (`weapon_origin_catalogue`, `goods_list_aggregator_detector`, `evasion_typology_detector`, `end_user_granularity`) are all DARK too. **This is the single most dangerous dark compliance path** — a banned-weapon (INF/CWC/BWC/Ottawa/CCM) miss ARIA cannot see or learn from.
- **Fix:** have `eliminated_weapons_watchlist` emit its own `capability_gaps.record_gap` on internal failure; remove the once-per-run flag for compliance-critical misses; remove the `except: pass` around the record_error.

### P0-4 — Cross-tier: the Node web tier reports NONE of its own failures to the brain
The "everything wired to the brain" directive's biggest hole. Observability is Python-brain-only.
- **server.mjs bridge forwards to a DEAD path:** `server.mjs:1791` POSTs `${BRAIN_URL}/api/brain/signal` — the brain only has `/api/aria/brain/signal`. 404 swallowed by `catch{}` (`:1796`), handler returns false `{status:'queued'}` (`:1798`).
- **server.mjs signals the brain on none of its own ops failures:** sweep-ingest fail (`:2068`), proxy timeout to aria-intel (`:2018`), Stripe/auth/route errors — all console.warn/503 only. Its sole error sink `errorTracker` (`lib/observability/errorTracker.mjs`) writes a local ring buffer + Telegram alert; **no brain path at all**.
- **WA listener env-var mismatch:** `aria_wa_listener.mjs:104` reads `BRAIN_SERVICE_URL || 'http://localhost:3117'`, but aria-wa is provisioned with `ARIA_SERVICE_URL=http://aria-intel.internal:8000` (`fly.wa.toml:11`); `Dockerfile.wa` sets no `BRAIN_SERVICE_URL`. Unless an undocumented secret was set, **every WA→brain call defaults to dead `localhost:3117`** — chat, read-document, AND the R-F887 signals. R-F887 fixed the path *string* but probably not reachability. (`localhost:3117` is also the wrong port — that's aria-web, not the brain's :8000.)
- **Fix:** point server.mjs bridge at `/api/aria/brain/signal`; add a single brain-forward hook in `errorTracker.record()` for structural/critical/auth severities (closes server.mjs + source-failure gaps at once); set `BRAIN_SERVICE_URL` on aria-wa or make the listener read `ARIA_SERVICE_URL`. Verify reachability live, not just the path string.

---

## P1 — should fix (real blind spots, lower blast radius)

### P1-1 — R-F887 incompletely rolled out: 8+ live callers still POST the dead bare path
Confirmed live: `POST /api/brain/signal → 404` still firing. Callers still on the dead un-prefixed path: `server.mjs:1791`, `services/aria_zoom_service.py:254`, `lib/whatsapp/waListener.mjs:1792`, `lib/whatsapp/ariaWhatsApp.mjs:197,679`, `lib/self/explorerScheduler.mjs:282`, `lib/aria/proactive.mjs:63`, `lib/aria/emailReader.mjs:245`, `lib/aria/linkedinIntel.mjs:324`. R-F887 only repointed the WA listener. **Fix:** sweep all callers to `/api/aria/brain/signal`; delete the `lib/whatsapp/*` duplicates per R-F832's stated intent.

### P1-2 — ~40 sweep sources DARK + a dead "push signals to brain" no-op
Per-source failures (`apis/briefing.mjs:215` → `errorTracker.record`) reach only the local pruner + Telegram, never the brain. The dedicated `pushSignalsToBrain` (`apis/briefing.mjs:524`) calls `redisPush`, which is a **no-op since Upstash was retired** (`lib/persist/store.mjs:35`) — yet logs `Pushed N signals` (misleading). Successful intel does reach the brain via `/api/aria/ingest`; this secondary queue is dead. **Fix:** route source failures through the errorTracker→brain hook (P0-4); delete or repoint the dead `pushSignalsToBrain`.

### P1-3 — `/channel/ingest` silently drops WhatsApp-mirror ingest failures
`routes/aria.py:16211-16214` — on `intel_ledger.add_signal` failure returns `{ok:False}` with no WARNING+ log and no brain signal; no `brain_hook.absorb` on success either (unlike `/webhook`). Group intel silently lost + brain never learns the path is broken. **Fix:** `_log.warning` + `capability_gaps.record_gap` on the except.

### P1-4 — Honesty guards are DARK (Phase A is the "Honesty foundation")
`premise_verifier.py`, `honesty_judge.py`, `self_claim_guard.py` — 0 wiring tokens. ARIA can't learn from her own honesty-guard trips/misses. (Note: `stream_honesty`, `tool_claim_guard`, `sanctions_claim_guard`, `ground_truth_guard` ARE wired.) **Fix:** emit a gap on every veto + a metric on pass.

### P1-5 — semantic_search.py (the wedge-central encoder) is DARK
0 wiring tokens; encoder failures/timeouts — the recurring event-loop wedge's epicentre — emit nothing. **Fix:** wire encode failure/timeout → `capability_gaps.record_gap`.

### P1-6 — RUN-EVAL-DAILY still disabled → eval regressions never become signals
`tasks.yaml:1100` `enabled:false` (R-F650 cost-blowout). ARIA's daily golden-set regression eval is off, so test/eval regressions can't feed the loop. **Fix:** re-enable (cost-gated) or wire eval-fail → gap.

### P1-7 — UI shows the Node sweep store, not the brain (no single source of truth)
Landing page after signin = `dashboard.html` (Node OSINT sweep: VIX/Brent/signals), zero brain figures. Brain truth (composite, grounded_rate, mastery, autonomy) is siloed one click away on `/aria-brain`. Two divergent stores, never reconciled. Honest-by-page but architecturally misleading. **Fix:** add a brain-state strip to the landing dashboard (fetch `/api/aria/autonomy/composite` + `/api/aria/health`), or make `/aria-brain` the landing page.

### P1-8 — adversarial_score 0.065 and 46.7h stale (live)
Health is `degraded` (reason: SUPERVISED mode). Adversarial freshness not refreshing — flagged in prior memory, confirmed live. **Fix:** trace the adversarial-refresh task / SUPERVISED-mute interaction.

---

## P2 — broad coverage / hygiene

- **P2-1 — ~56% of intel modules feed nothing** (117/263 have any wiring token; R-F886 didn't move this). self_diagnostic catalogue covers only ~16% (~42 of 263), so the ~210 uncatalogued modules — including every DARK engine above — are invisible to it. Broaden the catalogue toward the dark runtime safety engines.
- **P2-2 — self_coder post-deploy monitor reads an unwritten key.** `self_coder.py:72` `error_ledger:count` has no producer → `_monitor_post_deploy` is a permanent no-op; an auto-deployed bad fix would never trigger rollback. Point it at `len(error_log)`.
- **P2-3 — aria-web dashboard blackout on every aria-intel deploy.** Live: when aria-intel replaces, ~16 `/api/aria/*` proxy calls `fetch failed` + `brainBridge healthy→false`; the public dashboard goes dark until the health check re-passes, and the brain never learns it blinded its own UI.
- **P2-4 — 111 sub-WARNING swallows in routes/aria.py** (60 bare `pass`, 48 debug, 3 info) — structurally invisible to the brain. Most are best-effort enrichment (acceptable floor); the real one is P1-3.
- **P2-5 — `security_protocol.py`, `reasoning_library.py`, `regional_navigation.py`, `sanctions_divergence.py`, `regional_compliance.py` DARK** — runtime engines with no success/failure signal.
- **P2-6 — ARIA_CHAT_TRAIN_CAPTURE_TEXT dependency:** the hallucination extractor reads `entry["response"]` which is empty unless this env=1 (verify it's set on aria-intel, else that extractor is silent).
- **P2-7 — SELF-DIAGNOSTIC-15MIN cron is actually hourly** (`tasks.yaml:1709` `0 * * * *`) — cosmetic name/schedule mismatch.

---

## Suggested order
1. **P0-1** (unblock the loop — it already detects 43 gaps; make it able to act).
2. **P0-2 + P0-3** (compliance signals reach the brain — banned-weapon + DD layers; highest risk).
3. **P0-4 + P1-1 + P1-2 + P1-3** (cross-tier: one errorTracker→brain hook + path/env fixes lights up Node + WA + sources together — the core of "everything wired").
4. **P1-4 + P1-5 + P1-6** (honesty guards + encoder + daily eval → close the Phase-A-relevant blind spots).
5. **P1-7 + P1-8** (UI single-source-of-truth + adversarial freshness).
6. **P2** (broad coverage + hygiene).

## Cross-references
- Prior assessment this thread closed (R-F884..F888): `docs/ECOSYSTEM_360_OBSERVABILITY_HANDOFF_2026_05_25.md`.
- Wedge/infra: `memory/session_2026_05_25b_infra_brain_360.md`. DD/UI: `memory/dd_ui_lifecycle_review_2026_05_25.md`.
