# aria-intel full prospector — 2026-07-07

Read-only multi-agent prospecting sweep of aria-intel front + back end. 9 specialist agents + `pyflakes` static pass + live probes. Nothing was edited. Live build_rev at time of sweep: `ca4d240b` (2 commits behind `origin/main` `75975928`). `ARIA_STATE_HOTCOLD_SPLIT=1` confirmed set on the live machine.

Severity legend: **CRITICAL** = customer-facing wrong answer or cross-tenant leak; **HIGH** = safety/loop-integrity; **MED** = correctness/robustness; **LOW** = hygiene. CONFIRMED = proven at file:line. PLAUSIBLE = reasoned, needs live/runtime confirmation.

---

## Cross-cutting themes (read these first)

1. **The state_store storm is a SAFETY issue, not just latency.** Under the R-F2277 writer saturation, multiple controls flip **fail-OPEN**: the sanctions cost-cap bypasses its atomic reserve, the DD "blocked-web ≠ clean" guard reads a stale snapshot, the LLM cost-cap can `UnboundLocalError`, and search return-paths hang on unbounded writes. Fixing the writer ceiling is priority #1 for correctness, not just uptime.
2. **The self-improvement loop cannot ACT on its richest sink.** `record_gap()` is the sink §21e tells everyone to call, but (a) 7 callers pass invalid kwargs and their signals are silently thrown away, (b) 40+ gap-type strings aren't registered, and (c) the coder's `_TYPE_MAP` downgrades everything unmapped to observe-only, and (d) a failed fix locks the gap for 23h. The loop is enabled and wired but **near-blind to act**.
3. **never-false-clean holes survive on the EDGES.** The R-F1696/R-F2373 trilogy hardened the **company** main path, but the **person** path, **crypto** wallet paths, and the **canonical staleness** gate can each still emit CLEAR on a screen that didn't really run.
4. **§23 "green test / broken live" recurs.** Multiple guard tests use `**kwargs` mocks or drive a proxy entry point, so they stay green while the real path is broken (record_gap, person DD, sovereign timeout).
5. **Deploy churn is the operational amplifier.** 7 deploys in ~2h, each a ~10-min cold boot; it starves the autonomous engine (tick=0) and feeds the storm. No deploy-storm guard exists.

---

## CRITICAL

### C1 — Person DD fabricates "CLEAN — treat as clearance" when the sanctions source is down (CONFIRMED)
`aria_service/intel/dd_orchestrator.py:1410-1490` (`_run_identity_person`). The screen loop appends variants **unconditionally**, never checking `_scr.get("screened")` / `source_unavailable` (which `fuzzy_screen`/`screen_with_aliases` soft-return without raising). Result when OpenSanctions is breaker-open/rate-limited: `_screen_ok=True` → `derive_verified_sources(..., screen_succeeded=True)` stamps every list CLEAN → emits a `CONFIRMED` "treat as clearance under standard commercial PDD" finding → risk GREEN → **inflates grounded_rate** on a screen that never ran. Both downstream gates are bypassed for persons (the `SANCTIONS_SOURCE_UNVERIFIED` marker is only written on the company path at `:2225`/`:3042`; confidence gate `:6262` is `and not _is_person`). Guard test only drives the low-level helper, never `_run_identity_person`. **Gap-fixable (MODULE_BUG).** Fix: treat unavailable as not-screened, emit amber UNVERIFIED + write the data_gap, extend gate 6b1 to persons; cap-test must drive `_run_identity_person`.

### C2 — Autonomous coder is observe-only on capability_gaps (can-see-can't-act) (CONFIRMED)
`aria_service/autonomous/gap_detector.py:913-921` (`_TYPE_MAP`, 7 entries) + `:945` fallback → `self_coder.py:421` (`if severity < MEDIUM or not auto_fixable: continue`). Every gap-type string not in the 7-entry map falls back to `MISSING_CAPABILITY` (`auto_fixable=False`) and is skipped. So `module_bug`, `dd_layer_failure`, `search_backend_failure`, `llm_provider_failure`, `web_integrity_failure`, `code_generation_failure`, etc. are recorded but never attempted. This is the §21 P0 ("can see gaps but can't act"). **Gap-fixable / trivial:** extend `_TYPE_MAP` so actionable strings route to their auto_fixable `GapType`.

---

## HIGH

### H1 — Security: per-request auth flag stored in a MODULE GLOBAL → fail-open cross-tenant DD vault leak (CONFIRMED structural; timing exploit PLAUSIBLE)
`aria_service/routes/aria.py:230` `_AUTH_IS_INTERNAL=True` (module global, default unrestricted), mutated per-request by `require_aria_token` (`:437-439`, a sync/threadpool dep), read AFTER await boundaries by `_dd_owned_entity_ids` (`:1292-1298`). ARIA's constant internal-token traffic can race the global to `True` inside an attacker request's await window → `/dd/vault/search` (`:25562`), `/dd/case/{id}` (`:1309`) with no `user_id` returns **every tenant's** cases. Fail-OPEN (default `True` + race both resolve to unrestricted). **Fix:** replace with a `contextvars.ContextVar` / `request.state`, default fail-CLOSED.

### H2 — Sanctions: canonical staleness gate defeated by sustained refresh failure (CONFIRMED)
`aria_service/intel/sanctions_canonical/lookup.py:126-159` + `store.py:250-258` + `ofac_sdn.py:328-331`. `_freshest_refresh_age_seconds` derives freshness from the latest `refresh_log` row per source but **skips rows where `success=0`**. A failed daily refresh writes `success=False` and rolls back (old stale rows survive). After a sustained upstream outage, every source's latest row is `success=0` → all skipped → `age=None` → "unknown freshness = soft, don't downgrade" → **verdict CLEAR on 40-day-stale, actively-failing data.** **Gap-fixable:** compute age from newest *successful* refresh, or from `MAX(entries.last_refreshed)`.

### H3 — Sanctions: crypto-wallet paths read an unavailable index as "no match" (CONFIRMED, 2 sites)
`aria_service/intel/forensic_intent.py:256-258` (chat `crypto_wallet` tool) and `dd_orchestrator.py:8946-8956` (DD deterministic-primitives) both call legacy `screen_wallet()`, which returns `[]` for BOTH "no match" AND "index unavailable" (its own docstring says use `screen_wallet_checked`). Cold boot / redis error → user told a sanctioned wallet is clean, with no data_gap/amber. **Gap-fixable:** switch to `screen_wallet_checked`, branch on `source_unavailable`.

### H4 — record_gap() invalid-kwargs cluster: self-monitors are DARK (CONFIRMED, 7 sites / 5 files)
`capability_gaps.record_gap()` (`aria_service/intel/capability_gaps.py:208-217`) accepts `gap_type, detail, message_context, source, user_id, sector, severity, title` — no `module`/`description`/`gap_id`. Callers passing them raise `TypeError` on arg-binding, swallowed by `except: pass`/`logger.debug`: `deadlock_detector.py:176`, `memory_leak_detector.py:224`, `self_healing.py:1078`, `web_integrity_agent.py:921/964/1217` (one logs "gap recorded" that never runs — §22 breach), `email_reader.py:100` (also un-awaited). **ARIA is currently blind to its own deadlocks, memory leaks, contract violations, and web-integrity failures.** The `@fail_wire` then records the spurious `engine_failure`/"unexpected keyword argument 'module'" you see in logs. **Gap-fixable / trivial:** alias `description`→`detail`, accept+fold `module` into `source`; OR fix the 6 sites. Also `honesty_judge.py:546` creates a `record_gap` coroutine never awaited (fix: `ensure_future`, matching `:553`). Fix the guard test too — it uses `async def fake(**kwargs)` mocks that can't catch signature drift (§23).

### H5 — Coder never clears dedupe on a FAILED fix → 23h lock-out per gap (CONFIRMED)
`aria_service/autonomous/self_coder.py:630` marks the dedupe key (TTL `DEDUPE_WINDOW_SECONDS=23h`, `safety.py:72`) BEFORE the pipeline runs; non-success returns (`:638,:670,:1297`) never call `clear_dedupe` (only the engine-task path does, `tasks.py:1776`). One transient failure (DeepSeek cooldown, reproduce-gate, truncation guard) → gap locked ~22h. This is the live `duplicate_recent_run` blocking. **Gap-fixable:** on transient non-success, `safety.clear_dedupe("aria_coder_fix", gap.gap_id)`; keep the lock for genuine fixed/staged.

### H6 — NO_AUTODEPLOY_FILES protects a NON-EXISTENT path; the real gap_detector.py is auto-deployable (CONFIRMED)
`aria_service/intel/self_improve.py:148` lists `aria_service/intel/gap_detector.py` — that file doesn't exist; the real one is `aria_service/autonomous/gap_detector.py`. With `ARIA_SELF_IMPROVE_AUTO_DEPLOY=1` live, a `bug_fix` targeting the real gap_detector would auto-deploy with only the R-F904 truncation guard between a bad self-edit and disk — the coder editing the module that feeds it. **Gap-fixable / trivial:** correct the path.

### H7 — Search: DD "blocked-web ≠ clean" honesty guard reads a race-prone module global (CONFIRMED)
`aria_service/intel/web_search.py:115` `_LAST_SEARCH_ECOSYSTEM` global, written per-call `:1550-1563`, read by the DD guard `dd_orchestrator.py:4515-4536` (R-F1880). Under concurrent DD search it holds the *last-finishing* query's health (may be a cache-hit HEALTHY query) while the adverse-media search was DEAD; and cache-hits `:1386-1388` skip the snapshot entirely. Under today's SearXNG=0/DDG=202/429 conditions, the guard that stops "blocked web → clean bill" can silently not fire. **Gap-fixable:** return the snapshot alongside results / contextvar-scope it; populate on cache-hit.

### H8 — Search return-path awaits ~8 unbounded state_store writes per call (storm victim + amplifier) (CONFIRMED)
`aria_service/intel/web_search.py:1671-1689` (per-backend telemetry get+set), `:1734` RAG batch, `:1785` brain_hook — all `await`ed on the hot return path, none with a timeout. A wedged writer hangs every search after the answer is ready, and the telemetry itself adds ~8 writes/search to the pressure. **Gap-fixable (PERFORMANCE):** fire-and-forget telemetry / batch to one write / wrap in bounded `wait_for`.

### H9 — state_store hot/cold split does NOT relieve the hot-key write ceiling (CONFIRMED; flag confirmed ON live)
`aria_service/intel/state_store.py:449-458` — only 4 append-only prefixes (`audit:by_hash:`, `verified_facts:`, `verified_intel:fact:`, `reasoning_library`) move to cold. Every operational write-amplifier still shares the single hot writer: cost aggregates (`cost_tracker.py:420-442`, ~4-6 whole-blob RMW per API call, non-atomic, racey), mastery EWMA (`student.py:369-370`, whole-blob per observation), gap ledger, adversarial queues (`adversarial_challenge.py:1798`), portal creds (`portal_registry.py:795`). The split fixed DB **size/VACUUM**, not writer **throughput** — which is why the 07-05 "0 spikes >5s" claim doesn't hold today. **NOT cleanly autonomous** — needs a human design call (row-per-cell cost/mastery, or a second writer for churny operational prefixes). Mitigation available now: flip `ARIA_MASTERY_COALESCE_SAVE=1` (built, default off, flush wired — `student.py:382-384`).

### H10 — liveness_watchdog can false-restart a saturated-but-progressing boot (CONFIRMED structural)
`state_store.py:925-1002`, settle=120s (`:938`), ceiling=180s (`:937`); `probe_liveness` bounded at 3s (`:862-877`). Under write *saturation* (not a true wedge) the probe flush exceeds 3s → False; 120s settle < real ~10-min warm time (§11c-b) → watchdog can `os._exit(1)` mid-warmup, converting "slow" into a crash-loop-looking outage and resetting the clock. Can't distinguish wedged (restart helps) from saturated (restart hurts). **Fix (safety-sensitive):** raise settle above real boot time / gate os._exit on forward-progress (`knowledge_ready`).

---

## MEDIUM

### M1 — 40+ gap_type strings unregistered in VALID_GAP_TYPES (CONFIRMED; cross-cutting)
`capability_gaps.py:45-204` (117 entries) vs live callers → "Unknown gap type" WARNING (`:245`) on every occurrence; recorded anyway (not lost) but noisy and, via C2, downgraded to observe-only. Notable missing: `llm_provider_failure`, `llm_fallback`, `llm_unreachable`, `search_backend_failure`, `search_all_engines_blocked`, `all_general_web_dead`, `module_bug`, `dd_layer_failure`, `sanctions_source_unavailable`, `web_integrity_failure`, `deploy_verification_failure`, `code_generation_failure`, `contract_review_failure`, `tier_exhaustion`, `rate_limit_pressure`, `credential_expired`, `capability_test_missing`, `hallucinated_api`, +~25 more (full list in the autonomy agent transcript). `module_bug`/`dd_layer_failure` are especially wasteful — they name the auto_fixable GapType the caller wants but get downgraded. **Gap-fixable / trivial:** register + map. (Guard test `test_cooldown_persist_and_log_hygiene.py:229` uses a drifted hardcoded set — make it grep `gap_type=` across the tree.)

### M2 — LLM sovereign shadow path drops its own timeout + awaits inline (CONFIRMED; activation-blocking)
`aria_service/llm/model_router.py:199-201/267-270` passes `timeout=` to `aria_llm_provider.complete(prompt,*,system,max_tokens,temperature,**_kw)` (`aria_llm_provider.py:86-93`) which has no `timeout` param → dropped → hardcoded 120s (`:50,:157`). Also bypasses the health-checked wrapper (`resilience.py:357-425`, 12s clamp + `is_available()`), and SHADOW awaits the sovereign inline (`:255-264`) so a hung pod adds up to 120s/turn. Harmless while `ARIA_LLM_URL` unset (router byte-identical, CONFIRMED) but blocks the documented shadow ramp. **Gap-fixable:** fire-and-forget shadow, route through health-checked path, add a real `timeout` param.

### M3 — LLM cost cap can UnboundLocalError / go soft under the storm (CONFIRMED crash; PLAUSIBLE soft-window)
`aria_service/intel/cost_tracker.py:844-869` `assert_monthly_cap`: when `incrbyfloat(reserve_key)` raises (the storm) and cached spend ≥ cap, the `except` falls through to `if new_total >= cap:` but `new_total` is unbound → `UnboundLocalError` → user LLM call 500s instead of clean `MonthlyCostCapExceeded`. Common case (spent<cap): `except` returns without reserving → atomic ceiling bypassed, cap degrades to best-effort cache during saturation. Real overrun risk low (DeepSeek cheap, spend « $300). **Gap-fixable:** raise `MonthlyCostCapExceeded(spent=spent,...)` in the except, never touch `new_total`.

### M4 — get() serves up to 5s STALE values for ALL keys; set never invalidates (CONFIRMED)
`state_store.py:1916-1946` — despite the "error_log cooldown" name, the 5s cache (`_ERROR_LOG_COOLDOWN_S`) short-circuits **every** key before `_row`, and `set_key` doesn't pop it. `get→set→get` within 5s returns stale. Hits gap-dedupe (`capability_gaps.py:250`), cost reserves → double-recorded gaps, mis-gated cost. **Gap-fixable:** scope the cache to the actual error_log prefix or invalidate on write.

### M5 — Search "all general web dead" gap can't fire when SearXNG is configured-but-blocked (CONFIRMED)
`web_search.py:1751-1755` — the aggregate `all_general_web_dead` escalation requires `not _searxng_configured`. Today SearXNG is configured but returns 0, so the "general web is dark, operator action needed" signal never fires in the now-default regime. **Gap-fixable:** treat configured-but-blocked/breaker-open SearXNG as a dead state.

### M6 — bg_supervisor respawn budget is per-process-lifetime, never reset (CONFIRMED)
`main.py:271-301`, `_BG_MAX_RESPAWNS=5` (`:40`). Count only increments; a loop that dies transiently (e.g. `seed_knowledge` 1/5, observed live) accumulates over days until latched off "NEEDS OPERATOR" despite each death recovering cleanly. Also: critical infra loops (`liveness_watchdog :912`, `stall_detector :1378`, `expiry_sweeper :905`, heartbeat, dd_reconcile) are started WITHOUT a `factory=`, so the supervisor can't revive them at all. **Gap-fixable:** reset count on verified survival / sliding-window rate; register infra loops respawnably.

### M7 — Security: VLS proof/verify/chain endpoints have NO ownership check (CONFIRMED)
`aria_service/routes/aria.py:1653/1668/1680` (`/dd/vls/proof|verify|chain/{id}`) — missed by the R-F2097/2291/2402 ACL sweep. `canonical_entity_id` is deterministic/guessable (e.g. `company:BR:...`), so any token holder can confirm "has any tenant run DD on Company X, how many versions, when." Existence/cadence leak, not report body. **Gap-fixable:** apply the sibling `_dd_owned_entity_ids` gate, 404 on non-owned.

### M8 — Security: `/training-data/library-export` dumps all tenants' Q&A, not operator-gated (CONFIRMED brain-side; PLAUSIBLE reachability)
`aria_service/routes/aria.py:4663` (+`:4738`) returns up to 5000 cross-tenant `{question,response}` tuples, not in `_OPERATOR_ONLY_RE`. Reachability depends on the aria-web proxy allowlist (verify `server.mjs`). **Gap-fixable:** move `/training-data/*` behind `_OPERATOR_ONLY_RE`.

### M9 — Sanctions edges: OFSI stale mislabeled verified-clean + RCA relative source-unavailable dropped (CONFIRMED)
`dd_orchestrator.py:2904-2910` marks `uk_ofsi` verified whenever `not error`, but a stale snapshot has `stale=True`+`source_unavailable=True` with `error=None` → mislabeled verified-clean in the per-source table (headline protected by the direct-adapter backstop). `rca_screening.py:119-131` `_screen_one_relative` bare-excepts to `None` and treats a per-relative outage as "no hit" → inherited-risk false-clean (FATF R.12). **Gap-fixable.**

### M10 — Duplicate route handler shadowed (CONFIRMED)
`routes/aria.py:4460` and `:19405` both define `proactive_lead_hunt_ep` (pyflakes: "redefinition"). FastAPI serves the first, silently shadows the second (R-F2278 class) — one is dead; the 2×404 declared routes are this class, not dead frontend wiring. **Gap-fixable:** dedupe; run the R-F2278 route_audit gate.

### M11 — main.py: 7 background tasks created without retaining the reference (CONFIRMED via pyflakes)
`main.py:2662/2709/2777/2875/3060/3543/3589` — `create_task(...)` results assigned to locals never stored (`liveness_task`, `_portal_scheduler_task`, `guardian_task`, `health_precompute_task`, `brave_student_task`, `_wiring_monitor_task`, `deploy_proprio_task`). Un-referenced tasks can be GC'd mid-flight and silently cancelled — a plausible contributor to the "seed_knowledge loop is dead" respawns. **Verify then Gap-fix:** hold references in a module-level set (asyncio docs pattern).

---

## LOW / hygiene
- Frontend (aria-intel serves almost no UI; real UI is `public/*.html` on aria-web): client probe timeout 20s vs 8s proxy ceiling; one unescaped `innerHTML` on the inventory 0-hit path (`aria-brain.html:1130`, CSP-mitigated); R-F2438 hallucination panel missing its staleness flag; `scripts/verify_html.py` stale/cry-wolf. All LOW, Gap-fixable.
- Search: `search_entity` fires its 5 angles sequentially (`web_search.py:1878`, ~5× latency); RRF `url_key` strips querystring (procurement-notice recall loss, PLAUSIBLE); defence_event breaker records success on empty DDG-202.
- Sanctions: direct primary-source adapters are Latin-difflib only (no transliteration; mitigated by parallel OpenSanctions path). Direct-seed bypass of H1/H2 gates (latent, prod safe today).
- Core-Python: sync `sqlite3` on the loop in `agent_contract.py:260`/`agent_registry.py:116`/`agent_signup_vault.py:109` (dedicated low-traffic DBs); ~600 unused imports/vars (pyflakes) — 165 are `wire_failure` imported-but-unused (mostly the `@wired`-decorator refactor leftover, NOT dark paths — confirm before mass-cleanup).
- **0 undefined names, 0 syntax errors** across the whole backend tree (no repeat of the R-F2119/2120 31-error incident).

## DD items confirmed SOUND (no defect)
§22a doc-review routing (wins over external-tool keywords), R-F2412 placeholder-rerun block (fails closed 422), R-F2300/2250 async reconcile lifecycle, R-F2413 honest Layer-3 labeling (`independent_source_verification_run` stays False), R-F2401/2402 watchlist/owner-less fixes, CORS fail-closed, SSRF guard (`url_safety.py`) thorough with per-redirect revalidation, auth fails-closed in prod with constant-time compare, no hardcoded prod secrets, **no paid GPU fires unsupervised** (pod creation only in manual scripts; scheduler stop-only; terminate-in-finally), mined-tier eval honest/reproducible, truncation/rate-rollback/cost-cap coder guardrails intact.

---

## Recommended fix order

**Tier 0 — customer-facing correctness (do first):**
- C1 person-DD false-clean, H2 canonical staleness, H3 crypto wallet unavailable→clean. (never-false-clean edges — the product's core promise.)

**Tier 1 — cross-tenant security:**
- H1 module-global auth flag (fail-open DD vault), M7 VLS ACL, M8 training-data export.

**Tier 2 — restore the self-improvement loop (all trivial, unblock everything else):**
- H4 record_gap kwargs (un-blind self-monitors), C2 `_TYPE_MAP` observe-only, M1 register gap types, H5 dedupe-clear-on-fail, H6 NO_AUTODEPLOY path. Do these together — they're one coherent "make the coder able to act" batch.

**Tier 3 — state_store storm (root cause of the fail-opens):**
- H9 move hot write-amplifiers off the single writer (design call) + flip `ARIA_MASTERY_COALESCE_SAVE=1` now; H8 unbounded search writes; M3 cost-cap crash; M4 stale-read cache; H10 watchdog false-restart; deploy-storm guard.

**Operational (surface to operator, not code):**
- R-F2413 (`ef916819`, [deploy]) is committed + ship-marked but NOT live (build_rev `ca4d240b`). Batch-deploy once churn settles; verify build_rev advances. Throttle the redeploy cadence (7/2h) — it starves the engine (tick=0) and feeds the storm.
