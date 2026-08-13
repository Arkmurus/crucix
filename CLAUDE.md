# CLAUDE.md — session-binding rules for the crucix repo

**Read this at the start of every session.** Rules below are operator-codified and binding. Memory files extend them; this file is the floor.

## 1. Project state (verify at session start)

- **Phase**: A (Honesty foundation) per `docs/aria_platform_buildout_2026_05_10.md` + `memory/platform_buildout_north_star.md`.
- **Phase A exit gates** (7) — **probe `GET /phase/gates`; it beats this line.** Live 2026-07-16: #1 composite ≥71% ❌ OPEN (0.623, `low_confidence` — the ✅ here was STALE) · #2 heatmap floor ≥70% ❌ OPEN (0.507 — see below; the floor is NOT a competence measure) · #3 0 fly ERRORs/7d ❌ RE-OPENED 2026-07-15 by R-F2622 (was a FALSE pass) · #4 quarantined DDs closed ✅ HONEST as of R-F2643 (was a FABRICATED pass; now MEASURED from run_quarantine — live 4/4 investigated, genuinely passes — see below) · #5 env vars set ✅ (R-F794 set HARVEST+AUTONOMOUS+AUTONOMY_LEVEL; R-F2639 now checks all THREE — ACLED is DEFERRED per §18 and **never gated #5**, so the old "CANNOT close by code" was wrong) · #6 500-Q eval frozen ✅ EARNED 2026-07-16 by R-F2646 (operator pinned the set — count 500 + hash a07b6af7…; see below) · #7 ≥4 design-partner convos ❌ OPEN (0 logged; operator-owned, uncodeable — **Phase A cannot exit on code alone**).

- **Gate #4 — R-F2643 (2026-07-16): the ✅ was a FABRICATED pass. Gate #4 could not fail.** Both aggregators read `crucix:aria:dd:quarantined` and passed the gate on `len(...) == 0`. **A repo-wide grep finds NO WRITER for that key** — it never existed. `get_json()` returns `None` for an absent key *and* swallows store failures (the `redis_store.py:299-303` None-on-error contract), so `[] → 0 → pass=True` was unconditional, including on a wedged store. R-F2375 introduced it while fixing a different sentinel bug and called it "the REAL source" in a comment — the third gate to be certified by an absence. The honest source already existed: `run_quarantine.closure_summary()` (`_KEY = crucix:aria:quarantined_runs`), documented in its own docstring as the "Phase A gate #4 closer surface", which answers what the gate actually asks — are quarantined runs **investigated**, not "is the list empty" — and whose `gate_passes` requires `len(items) > 0` so an empty store cannot pass it. **Live-verified 2026-07-16 @3b4c4f9c: total=4, closed=4, open=0 → the gate GENUINELY passes** — the fabricated ✅ turned out to coincide with a true ✅, but it is now EARNED and falsifiable (any un-investigated run re-opens it), where before it was `pass=True` no matter what. Do not close it by pointing back at a key nothing writes.

- **Gate #6 — R-F2640 (2026-07-16): the ✅ was never earned; "frozen" was never measured.** Two aggregators reported gate #6 and BOTH were wrong, in opposite directions: `main.py` passed it on `len(get_golden_set()) >= 500` — the SIZE of a mutable, appendable list, which cannot detect mutation at all — while the `routes/aria.py` fork read `crucix:aria:eval:500q:status`.frozen, a key **nothing in the tree ever wrote**, so it was structurally UNCLOSEABLE. R-F2640 measures the **pin**: `eval_runner.freeze_golden_set()` records count + a content hash, and the gate passes only while the live set still matches. Any drift (entry added/removed/reworded) RE-OPENS it — that is the point of freezing a benchmark. **CLOSED 2026-07-16 (R-F2646):** the operator directed the pin; executed via `POST /api/aria/eval/golden/freeze` (operator-token, run from localhost inside the box so the token never left the machine). Live-verified on BOTH surfaces: gate #6 `pass=True, reason=frozen_and_intact`, `pinned_hash==live_hash=a07b6af760ad7f44`, `live_count=pinned_count=500`. **Any edit to the golden set (add/remove/reword) now RE-OPENS the gate** — that is intended; re-pin after a deliberate set revision. The pin is a durable no-TTL state_store write, so it survives restarts. Do not "fix" a future `not_frozen`/`drifted` reading by restoring the size check — that reintroduces the fabrication.

- **Gate #5 — R-F3640 (2026-08-02): the ❌ was a FALSE NEGATIVE; the gate could not see the control plane.** Measured live: fly secret `ARIA_AUTONOMOUS_ENABLED=0`, but the durable override `crucix:autonomous:enabled_override='1'` was set and the engine was **genuinely running at L3** (`/health` → `enabled:true, running:true, level 3, 98 tasks`). Gate #5 read `os.environ` ONLY, so it reported the platform unconfigured while autonomy was ON. `engine.is_enabled()` documents the precedence — the override wins over env **in BOTH directions**, and `/autonomous/enable` exists so the switch can flip without a redeploy. Now the master switch resolves to **effective state** (override `"0"/"1"` wins, else env), with `by_var_source` + `env_var_value` reported so **a pass earned by the override can never be mistaken for a pass earned by the secret**. `ARIA_OUTPUT_HARVEST_ENABLED` + `ARIA_AUTONOMY_LEVEL` stay **env-only** — they have no override, and letting one satisfy them would WIDEN the gate. Store unreadable → an override could exist either way → `pass=None` (unknown), never False. Read with `get_strict` (mirroring `engine.refresh_runtime_override`), **not** `get_json_strict` — the value is a bare string and the json layer swallows a parse failure into `None`, silently falling back to env and reproducing the same false negative. **Note §17 still records the secret as `=1`: docs and live have DIVERGED and the durable override is the only thing keeping autonomy on** — clearing it would take autonomy dark.
- **Gate reporting — R-F2639 (2026-07-16): there is ONE measure now. Do not fork it again.** `GET /phase/gates` (main.py, unauthenticated) and `GET /api/aria/phase/gates` (routes/aria.py, Bearer-gated) both **render** `intel/phase_gates.py::compute_phase_gates()` and measure NOTHING themselves. Before R-F2639 they were independent and disagreed per-gate: the fork served **the exact vacuous gate-#3 pass R-F2622 killed** (`get_error_count(7) == 0 → closed`, so an empty/evicted ledger read as a clean week), and **closed operator-owned gate #7 from ARIA's own distinct chat sessions** (value 200 vs the honest 0). Note the fork was not uniformly worse — its gate #5 was the STRICTER one (3 vars incl. `ARIA_AUTONOMY_LEVEL`), so R-F2639 took the honest reading **per gate** rather than adopting either fork wholesale. `pass` is tri-state and load-bearing: `True`/`False` = measured; `None` = COULD NOT MEASURE, rendered `unknown`, never `open` — "could not measure" is not "measured and failed". A regression test asserts both handlers call the canonical measure, so a third aggregator cannot silently reappear.

- **Phase A DEEP DD (2026-07-16) + 3 fixes shipped.** 3-agent deep dive (see `memory/phase_a_deep_dd_2026_07_16.md`): 3/7 closed (#4/#5/#6); exit needs all 7 and **#7 is operator-only, so no code exits Phase A.** Three integrity fixes landed: **R-F2663** — gate #3 was structurally un-closeable because every boot's `[R-F2122] heavy graph warmup failed` ERROR (a 5s timeout on a ~10-min load, `main.py`) reset the 7-day streak; now a generous background timeout + WARNING (not ERROR; `is_reset_type` excludes `log:warning`) so the streak can accrue. **R-F2668** — with R-F2663's FALSE reset gone, the REAL next gate-#3 blocker surfaced: `_seed_knowledge_bg` (`main.py`) is a ONE-SHOT but `_singleton_task` respawn-registered it, so its NORMAL completion looked like a death → re-spawned 5× → the `[R-F1610] … NEEDS OPERATOR` ERROR reset the streak every boot; now `_singleton_task(..., respawn=False)` for one-shots (keeps the R-F2073 singleton lock; genuine while-True loops still self-heal). **R-F2664** — gate #2 heatmap DATA-LOSS: `_load_regional_mastery` used non-strict `get_json` → a slow-boot StoreReadError poisoned `_regional_cache` to `{}` → the next `update_regional_mastery` CLOBBERED the durable key; now strict-read + skip-on-deferred so a not-ready store can't wipe mastery (explains the empty live heatmap). **R-F2665** — gate #1 `pass` now requires `not low_confidence` so a mastery-ONLY 0.71 (honesty axis unmeasured, 70% of the weight) can't falsely certify.
- **Gate #3 — R-F2622 (2026-07-15): the pass was FABRICATED, now honest.** R-F560 certified gate #3 whenever no ERROR was found in the error ledger — including an EMPTY ledger — and hardcoded `consecutive_clean_days=7`. The ledger is a 200-slot ring buffer SHARED with warnings (`self_improve.py:1792`) with a 7d TTL, and `record_error` drops errors while the R-F1510 breaker is open, so "no ERROR found" was equally consistent with a clean week, an evicted error, a TTL'd key, or dropped errors. R-F2622 measures the streak from a durable TTL-less anchor written at `record_error()` time and reports `pass=False` + `insufficient_history` when 7 days cannot be PROVEN. **The gate is now EARNED, so expect it to read pending until real evidence accrues. Do not "fix" that by restoring an assumption.**

- **Gate #2 — the floor is measuring STARVATION, not capability (verified 2026-07-15).** Live floor is **0.507**, not the ≈0.263 previously recorded here (that figure was stale). 0.507 is not a competence score: `INITIAL_MASTERY = 0.5` (`student.py:90`) and one chat observation at `default_weight=0.15` (`aria_engine.py:120`) gives `alpha = 0.1×0.15 = 0.015` → `0.5 + 0.015×(1.0−0.5) = 0.5075` → `round(...,3)` = **0.507 exactly**. The five floor cells all share 0.507 because each has had ONE touch. **86 of 87 cells are below 0.70; only technical×europe (0.708) passes.** Two structural problems before any grinding is worth doing:
  1. **The dominant signal is not competence — FIXED by R-F2660 (2026-07-16).** The R-F1744 reading loop (~9.6 sessions/day) passed **`correct=True` hardcoded** (`student.py:1318`) whenever region text was merely FOUND — measuring reading VOLUME, not comprehension. R-F2660 replaced it with the SAME honest recall grade the tasks.py research bridge uses (`autonomous/tasks.py::_grade_researched_cell`): the reading loop now credits regional mastery ONLY if the local reasoning stack can actually ANSWER about the cell and overlap what was read; a failed grade credits `correct=False` (does not lift), a grader error SKIPS the update (never fabricates a pass). **EXPECT THE HONEST FLOOR TO DROP** as cells previously credited for reading get re-graded on real recall — that is the gate becoming EARNED, NOT a regression; do not "fix" it by restoring the trophy. **STILL OPEN (R-F2661, deferred by operator): a SECOND reading trophy at `student.py:1581`** (R-F196 article→regional mastery) also credits `correct=True` for reading and still inflates gate #2 at lower (per-article) volume — honest-grade it next, cost-guarded.
  2. **`get_regional_heatmap` only iterates cells that already have samples** (`student.py:2136`), so **137 of 224 cells (61%) are invisible to the gate**. The gate is therefore currently EASIER than honest, and perversely PUNISHES breadth: new cells enter at 0.5 and DROP the floor. Expect the honest floor to fall below 0.507 when they appear — that is correct, not a regression.
  **NOT clamping** (binding): do not close this by dropping "artifact" regions, by the `floor_breach_cells[:20]` truncation, by leaving `ARIA_STUDENT_SEED_ALL_REGIONS` off to keep 137 cells hidden, or via the `mastery_weight` seed knob (`routes/aria.py:714`). Each closes the gate by measuring less.
- **No out-of-phase work**: refuse Phase B+ until ALL Phase A gates close. Operational R-numbers always allowed. Operator override requires explicit "I understand Phase A gate #X is open. Override anyway."
- **ROOT CAUSE, NOT SYMPTOM — BINDING (operator directive 2026-06-12):** Never apply a band-aid (timeout increase, retry count bump, cooldown extension) without first doing a deep-dive investigation to identify and fix the root cause. Every issue must produce a structural fix that eliminates the failure class, not a patch that hides it. If you catch yourself raising a timeout or adding a retry, stop and ask: "What is actually slow/breaking, and why?" Fix that instead. This rule is codified in AGENTS.md and applies to both Claude and ARIA.

## 2. R-number discipline (R-F540 reservation log)

- **Every change gets an R-number.** No exceptions.
- **Reserve before code**: `python scripts/admin/reserve_r_number.py reserve "short title"` writes to `data/r_number_reservations.json`. Git serialises further.
- **Mark shipped at push**: `python scripts/admin/reserve_r_number.py ship R-F<n> <sha>`.
- **Why this exists**: 9 R-number collisions in 50h (2026-05-13..05-15) — every collision needed a rename pass. Don't claim a number by writing it in a comment; claim it via the registry.

## 3. Verify-after-fix (binding)

**Loop**: MAP → FIX → VERIFY PASS 1 → PATCH → RE-VERIFY PASS 2 → COMMIT → PUSH → live smoke.

- Pass 1: audit call sites + signatures against 8-section checklist (calls, defs, fields, conditions, regex, concurrency, env flags, imports).
- Pass 2: fresh agent re-tests WHOLE CHAIN for regressions introduced by Pass 1 patches.
- Exempt: `MEMORY.md` only. Code/tests get both passes.
- Commit message includes `Verified-by: parallel-agents (2 passes)` or `Verified-by: manual-read (2 passes)`.
- Source: `docs/verification_protocol_2026_05_11.md`. 2026-05-11 sweep found 16 hidden bugs in 56 fixes (29% defect rate).

## 3b. Function-name verification (R-F1069 — binding)

**Before writing ANY call to a function, verify it exists.** This rule exists because I shipped wrong function names twice in one session (get_current_risks, get_current_state — both didn't exist).

**Workflow:**
1. Before writing `await module.function()`, run: `grep -n "def function\|async def function" path/to/module.py`
2. If the function doesn't exist, find the real name by grepping for `def ` in the module
3. Check whether the function is sync or async — don't `await` a sync function
4. Document the verified function name in a comment if it's non-obvious

**Exception**: Standard library and well-known third-party packages (httpx, asyncio, json, os, sys, re, time, datetime, Path, logging) are exempt.

## 3c. Capability test requirement (R-F1069 — binding)

**Every fix MUST include a capability test that invokes the broken path.** A unit test that tests a helper function does NOT count. The test must:
1. Call the actual function that was broken (build_report, generate_market_intelligence, attempt_recovery, etc.)
2. Assert the user-visible outcome (returns a dict, doesn't raise KeyError, etc.)
3. Be run BEFORE the fix to confirm it fails, and AFTER the fix to confirm it passes

**Verified-by is only truthful when the test invokes the broken path.**

## 4. Chain-aware test-retest

Before code: map downstream chain (who calls this, what state does it write, what reads that state). After deploy: probe LIVE, not just unit-test.

## 5. Unit + capability tests per R-number

- **Unit test** proves the function's contract.
- **Capability test** proves the user-visible symptom is fixed (often via FastAPI TestClient or real-fixture replay).
- Calibrated 2026-05-11 after R-F291 close shipped only unit tests and missed the live symptom.

## 6. ARIA mirrors Claude — native, not third-party

If Claude Code doesn't depend on it, ARIA shouldn't either. Files + LLM only. No paid persistence (Upstash cancelled 2026-05-12). No paid OpenSanctions (declined 2026-05-15). No paid OpenCorporates (declined 2026-05-12). Burden of proof on any new third-party.

## 7. ARIA has infinite memory

No TTL on knowledge. No oldest-first prune. No eviction. Overflow → cold storage, never delete. Self-study writes must never be paired with prune (R-F173 was a violation, reversed by R-F238).

## 8. Map-then-change

Read the area of change before editing. Don't pile on fixes without tracing the chain. Don't introduce abstractions beyond what the task requires.

## 9. Lifespan smoke test before push

2026-04-27 outage: F28 broke prod via Python local-var scoping; 1109 unit tests passed but lifespan failed at boot. Smoke-test `lifespan()` locally before push for any `main.py` or boot-path change.

## 10. Batch findings before fixing

On long log pastes from operator: enumerate ALL findings first as a numbered list. Don't commit until operator picks which subset to batch.

## 11. Deploy after commit — you own the full pipeline (R-F1145)

Unpushed commits aren't deployed. After commit, YOU deploy directly to fly.io:

**Windows (PowerShell):**
  `.\scripts\deploy.ps1 --all`  (mirrors deploy.sh exactly: push guard + build_rev verify + health checks)

**Linux/macOS (bash):**
  `./scripts/deploy.sh --all`   (batches all pending R-numbers, avoids cold-boot storms)

**Fallback (any platform, when the script is broken, or when a PEER has uncommitted work):**

⚠️ **A `[deploy]` COMMIT MESSAGE DOES NOTHING.** This section told you to add `[deploy]`
and push. It is FALSE and cost a live deploy on 2026-08-01: R-F3634 was pushed with the
tag, no workflow ran, and the fix sat un-deployed while the operator was told it was in
flight. `.github/workflows/deploy-fly.yml` has **no `push:` trigger** — only
`workflow_dispatch`. The `if:` guard checking for `[deploy]` in the commit message is
real code and is **unreachable by construction**; R-F3238 found this and recorded it in
a workflow comment, but nobody corrected THIS file, which is the one every session reads.

  1. Dispatch the workflow — it builds from the **pushed SHA** and never touches the
     working tree, so a peer's uncommitted work cannot be stashed, shipped or lost:
     ```
     git push origin main
     gh workflow run deploy-fly.yml --ref main -f reason="<auditable justification>"
     gh run list --workflow=deploy-fly.yml --limit 1     # confirm it STARTED
     ```
     `reason` is required and audited. Confirm the run's `headSha` is YOUR commit.
  2. Verify live: `curl https://aria-intel.fly.dev/health/live` — confirm `build_rev`
     matches your commit SHA. **A dispatched run is not a deploy until this passes.**

**When to prefer dispatch over `deploy.ps1`:** `deploy.ps1` builds from the WORKING TREE.
With a peer agent active and files dirty, it would ship their untested WIP to production;
`-CleanHead` stashes their work and has been observed DESTROYING it while reporting
success (see `two-agents-one-tree-hazard`). Dispatch is the correct route whenever
`git status --porcelain -- aria_service` is non-empty and the changes are not yours.

**NEVER use raw `flyctl deploy`** — it bypasses the push guard, build_rev verification, and batching. The only exception is an emergency hotfix where BOTH deploy scripts are broken.

**Deploy verification (binding — anti-hallucination law #4):**
  A deploy is NOT done until you have PROVEN it live. The sequence is:
  1. Run the deploy command (deploy.ps1 or deploy.sh)
  2. **Check the exit code** — non-zero = not deployed. Read the output.
  3. **Live-smoke it** — curl the app's `/health/live` and CONFIRM the `build_rev` matches your commit SHA
  4. If the live version did NOT change to your commit, you did NOT deploy — say so honestly
  5. Only then ship-mark: `python scripts/admin/reserve_r_number.py ship R-F### <sha>`

**If the deploy build times out (torch is the bottleneck):**
  - The build is still running on Depot — wait for it to complete
  - Check `flyctl apps releases -a aria-intel` for a new version
  - If it truly failed, add `[deploy]` to the commit message and push again
  - Do NOT ship-mark the R-number until the deploy is verified live

## 11c. Pre-deploy compile gate + slow-boot diagnosis (R-F2126/R-F2122 — binding, 2026-06-28)

Codified after a multi-hour aria-intel outage. Two lessons, both binding:

**(a) NEVER deploy without compiling the WHOLE tree first.** ARIA's autonomous annotation campaigns (R-F2119/R-F2120) pushed **31 syntax errors** to `main` — comments inserted mid-expression (`httpx.AsyncClient(timeout  # no-breaker:…=3.0)`) and stray tokens in code-gen templates — making the entire tree un-importable. The live brain survived only because it predated the corruption. Before ANY deploy (manual or reviewing ARIA's), run a full-tree gate and refuse to deploy on any failure:
```
find aria_service -name "*.py" -not -path "*/tests/*" | while read f; do python -m py_compile "$f" || echo "BROKEN: $f"; done
```
An autonomous agent (or anyone) reporting "safe to deploy" is NOT sufficient — **independently compile-verify** (§23). Compile-green ≠ correct, but compile-red = guaranteed boot failure. The deploy scripts should enforce this gate; until they do, run it by hand.

**(b) A slow boot is NOT a crash loop — wait the full boot before diagnosing or restarting.** aria-intel's boot loads ~223k facts + ~1.2M neural edges + 681k state keys **synchronously** → ~10 min to `/health` green (R-F2122 now defers the heavy graphs to background warmup). During those 10 min the machine shows `critical`/`000` but is NOT dead. **Restarting resets the 10-min clock** and prolongs the outage; a "patient" poll must EXCEED the real boot time. Before declaring a boot hung: check the boot log for forward progress (the last init that logged), confirm fly is actually SIGTERMing it (version/timestamp changing) vs just sitting `critical`, and only then conclude. Contested deploy leases (multiple agents/CI racing) SIGTERM each other's in-flight boots — coordinate one-deploy-at-a-time.

## 12. Check fly logs first

At session start, ask operator for latest fly logs OR fetch via `gh` / flyctl. Prioritise from production reality, not from backlog assumptions.

## 13. Stream-bypass rule

`aria_chat_stream` is a subset-fork of `aria_chat`. Every new post-response hook (guard, audit, capture) must be mirrored into BOTH paths. R-F557 audits the current state; future hooks must keep both in sync.

## 14. Fallback transparency

When a provider cools down and a fallback serves, ARIA reports "operational", never "degraded". Cooling ≠ broken.

## 15. Pay-once-remember-forever

Every paid API call (Brave/Anthropic/DeepSeek) writes its output to `brain_hook` + `rag_store` + `intel_ledger` so the next equivalent query hits memory for $0.

## 16. Local dev environment + Fly inventory

- Python venv: `<repo>/.venv`. Activate: `.venv\Scripts\activate.ps1`. (Was documented as `C:\code\crucix\.venv` / Python 3.14.3; that machine is gone. Do not hardcode a checkout path here or in tests — use `_source_probe.repo_path()`.)
  - **Windows/ARM64 caveat (rebuild 2026-08-03, Python 3.13.14):** 5 of `aria_service/requirements.txt` publish no win-arm64 wheel — `PyMuPDF`, `chromadb`, `opencv-python`, `sentence-transformers` (torch), `faster-whisper`. All are import-guarded, so the service boots and `/health/live` returns 200 without them; PDF-via-fitz, RAG, OCR preprocessing, embeddings and voice transcription are inert locally and must be exercised in the Linux image. `uvicorn[standard]` also fails (`httptools` has no arm64 wheel; `uvloop` is POSIX-only and never installs on Windows) — plain `uvicorn` is correct here and simply uses the pure-Python HTTP parser.
- Run tests: `python -m pytest aria_service/tests/ -v`.
- **CURRENT BASELINE — 89 failed / 14,610 passed / 14,699 collected across 1,731 files. Measured 2026-08-09 at `e68f0088` (R-F3818): `VALID=YES`, tree hash `8acea472e2979109` identical before AND after, recorded by `scripts/admin/suite_baseline.py --single-process --record`, and it is the first baseline to carry an ENVIRONMENT fingerprint (R-F3794: python 3.13.14, 121 packages, `0871fe4d97709643`, fastapi 0.141.1).** Full set + all 89 node ids: `docs/suite_baseline.json` (machine-read by the gate) and `docs/suite_baseline.md` (ONE file — do not create a fourth). Command: `python -m pytest aria_service/tests/ -q --tb=line -p no:cacheprovider --timeout=600`, single process, no env overrides, network guard ON.
  - **The old headline here read `112 / 13,725 @ 0c3e853d` and was a THIRD figure that matched neither the JSON nor `suite_baseline.md` (both of which said 103 @ `cd522878`). Corrected 2026-08-09.**
  - **TWO baseline entries were FALSE FAILURES — a red test can be the defect (R-F3858/R-F3859, 2026-08-11). Expect 88, not 90, at the next local re-record.** Both sat in the recorded set as "known", which is what let them rot: a permanently-red test can never go green, so it can never carry information either.
    - `test_rf2059_backend_hardening::test_all_search_backends_have_circuit_breakers` reported `_search_searxng` as unwired while that function has TWO failure wires. Two independent heuristic faults: it scanned a **fixed 80-line window** from the `async def` (function at 662, its literal `wire_failure(` at 754 — twelve lines out of reach, so GROWTH alone blinded it), and it matched the **literal name**, so the in-window call through `wire_failure as _wf1657` was invisible. Now AST-based, resolving aliases and walking the whole body — and it carries a test proving it still DETECTS a genuinely dark backend, because a guard that cannot fail is not a guard. Same classes as R-F3597 (line-number fragility) and R-F3791 (a guard that goes blind rather than fails).
    - `test_rf1656_1657_capability::TestBackendNames::test_backend_names_no_brave` asserted a **REVERSED policy and pointed at the defect**. R-F1657 forbade a *phantom* "brave" when Brave was a removed stub (R-F320); Brave is now the paid primary (R-F2318) and sole DD engine (R-F3847). The obvious way to green a red test is to delete the offending line — here `["brave"] if _brave_on else []` — which would silently disable primary search. It now asserts the surviving intent (a name may appear only when the backend can serve, i.e. gated on `_brave_on`) and its failure message says explicitly not to fix it by deleting the name.
  - **Set diff 2026-08-01 → 2026-08-09: 87 standing · 16 FIXED · 2 new.** Both "new" (`test_rf2507_brain_queue_integration::test_drain_failure_retries`, `test_store_fact_skip_rag::test_store_fact_default_runs_rag_ingest`) PASS standalone, so they are order-dependent, not deterministic regressions. **Cause not established** — a hypothesis worth checking is R-F3816, which now emits a `route_audit` brain signal during `lifespan`, and both tests touch brain-queue / RAG-ingest paths.
  - **MEASURE ON A QUIET WORKTREE, not the main checkout (R-F3818).** Two attempts in the main checkout failed for two DIFFERENT reasons and both were caught by the tool, not by inspection: the first was `VALID=YES` but **refused to record** because two tracked files had uncommitted changes, so the `commit` label would have named a sha that did not contain what ran; the second read `VALID=NO` because the peer agent rewrote those same files mid-run. `git worktree add <path> <sha>` gives a tree the peer cannot reach, so validity and label are both guaranteed. Verified equivalent to the main checkout before relying on it (identical 1-failed/118-passed on a data-dependent subset). Copy the recorded JSON back into the main checkout to commit it.
  - **NEVER QUOTE A FULL-SUITE NUMBER WITHOUT A VALIDITY RECORD (R-F3597).** `inspect.getsource` slices the file at the line numbers captured AT IMPORT. On a shared tree a peer commit landing mid-run shifts them and it returns **a different function's body** — SILENTLY, because the wrong slice is still valid Python. This corrupted two attempts at this baseline: run 1 read **147** (4 peer commits touched `routes/aria.py` + `aria_engine.py`) and run 2 read **110** (5 commits, one mine). The failure-set diff proves it was never a regression: `rf409` had 8 failures in run 1 and **0** in run 2. **The real figure was ~110 throughout.** Measure with `scratchpad/measure.py` (SHA-256 over every tracked `aria_service/**/*.py`, before and after) and **ask for a quiet tree first**. `VALID=NO` means DISCARD, not publish.
  - **`_source_probe.py` is the fix, not a workaround**: resolve a function BY NAME through the CURRENT file's AST. Applied to the 5 proven victims (15 failures under run-1 conditions). It removed ZERO from a CLEAN run — because those tests were never failing in one. A prediction of ~95 was wrong for exactly that reason.
  - **Two flaky entries in the 112, not fixed:** `test_rf2144_chunked_knowledge_load` (loop starved 345ms) and `test_rf2200_neural_index_offload` (stalled 258ms, limit 250ms). Both pass in isolation; both assert event-loop latency against a hard threshold. A faster box would make them PASS, so "the machine was idle" does not explain them — undiagnosed.
  - **OPEN, three-line reproducer:** a bare `from aria_service import aria_engine` in ANY earlier test makes mem0 recall return EMPTY, costing `test_rf3489_mem0_recall_is_owner_scoped.py` 5 tests. It passes 12/12 alone.
  - ✅ **THE GATE IS NOW WIRED AND PROVEN FIRING (R-F3826, 2026-08-09/10).** `ci.yml` has a `suite-baseline-gate` job running `scripts/admin/suite_baseline.py` (R-F3373). Three things had to be true first and each was measured: it needs **its own job** (the `test` job caps at 25 min; the run takes 18m21s in CI, ~40 min on this box), **its own dep set** (`aria_service/requirements-ci.txt` = the manifest minus five heavy import-guarded packages; zero tests import them at module level and R-F3795 skips the rest), and **its own baseline** — `docs/suite_baseline.ci.json`, recorded IN CI. **Never gate CI on `docs/suite_baseline.json`:** that file is stamped `win32/ARM64` and the SAME code yields **89 failures there and 165 in CI** — 76 phantom "regressions" from platform alone. To re-record: dispatch `ci.yml` with `record_baseline=true`, download the artefact, commit it.
    - **KNOWN-FLAKY SET — the gate WILL go red intermittently on an unchanged tree. Check here BEFORE hunting a commit.** All pass standalone; all observed flipping across runs and platforms: `test_store_fact_skip_rag::test_store_fact_default_runs_rag_ingest` · `test_rf2507_brain_queue_integration::test_drain_failure_retries` · `test_rf795_brain_hook_tier_timeout` (both tests) · `test_rf1839_wa_brain_contract::test_brain_routes_non_failure_signal_to_absorb_not_gap` · `test_rf3768_tooluse_dpo_cycle::test_orchestrator_pins_inputs_and_bounds_paid_artifact_recovery` · `test_rf3362_operational_gap_staleness` (both tests). **The brain-hook family dominates**, which is the lead worth pulling. R-F3841 added index isolation to the store_fact one and it FAILED AGAIN in the next CI run — so that hypothesis is disproven; its docstring records what was already ruled out. Fix them; do not mute the gate.
    - **FLAKE INVESTIGATION — what has ALREADY been ruled out (R-F3841/R-F3846). Do not repeat these:**
      1. **Order within the brain-hook family** — running `rf795` + `rf2507` + `rf1839` together passes in BOTH file orders (18/18 each way).
      2. **`knowledge` dedup-index leakage** — R-F3841 added an autouse fixture resetting `_topic_index`/`_content_index`/`_index_count`. Seeding the pollution does NOT reproduce the failure (`_ensure_indices` rebuilds on any count mismatch), and `test_store_fact_skip_rag` **failed again in the very next CI run with the fixture in place**. Disproven.
      3. **`brain_ingest_queue` singleton leak** — `connect()` closes any prior handle before rebinding and `close()` resets `_conn = None`. Both read; both correct.
      4. **A 15s wall-clock dependence in `rf795`** — `_TIER_TIMEOUT_S` defaults to 15.0s, but the test patches it (12 references) and the whole file runs in 1.24s. Disproven by measurement.
      **What remains:** a polluter elsewhere in the full suite. The next step is bisection over the collection order preceding a failing file — expensive but tractable. **Do not add another isolation fixture on a hypothesis; reproduce first.**
    - ⚠️ **NEVER re-record a baseline while a regression is live.** R-F3842: the mid-session re-record captured three wiring-gate failures caused by my own stolen-decorator defect, i.e. it would have enshrined the regression as "known good". Both baselines were discarded and re-recorded on the fixed commit. A stale baseline is recoverable; a baseline that blesses a defect is not.
    - **Both baselines are recorded at `168674b2`:** local `90` failed (win32/ARM64), CI `162` (linux/x86_64). The 72-failure gap is PLATFORM, not code. Refresh it from a `VALID=YES` run before enabling it.
    - **CORRECTED 2026-08-03.** This line said the JSON "is STALE (2026-07-28)". It is not: the file reads `recorded_at: 2026-08-01`, `commit: cd522878`, `valid: true`. Worse, its `totals` are **103 failed / 13,850 passed / 13,953 total**, which contradicts the 112 / 13,725 headline three lines above — two different runs, same date, different commits, and the doc quotes one while pointing at the other. Trust the JSON (machine-read by the gate); treat the headline as prose until a `VALID=YES` run reconciles them.
  - **A quiet tree is not optional, and on this repo it is rare.** Attempted a §16 measurement 2026-08-03 21:41–22:12: `VALID=NO`. The peer agent landed four commits mid-run (`d05ed69a`, `6f0d4dbb`, `fdbd6e61`, `d0d897d5`) — the last of which was itself a note flagging that its commits would invalidate the run. **`git status` cannot detect this**: a commit CLEANS the tree, so a "clean" check between commits reads green while the instrument moved underneath. Only the before/after tree hash catches it. Measure the hash, never the status.
  - **Diff the failure SET, never the count alone.** The count moves legitimately: 1,122 tests were added between 2026-07-30 and 2026-08-01.
  - **A baseline diff across a VENV REBUILD is a code-delta PLUS an environment-delta, and nothing separates them for you (R-F3791, 2026-08-08).** The 2026-08-08 run (`VALID=YES`, 126 failed) diffed against the 2026-08-01 JSON (103 failed) yielded "36 new failures". **At least 5 were caused by no commit at all**: this box's venv was rebuilt 2026-08-03, and under the FastAPI it now resolves, `include_router` stopped copying a sub-router's routes into `app.routes` — so five tests that prove a route is registered by walking that list read a 770-route app as having four. Live routing was never affected (`/health/live` → 200, POST-only `/api/aria/brain/signal` → **405**, `app.openapi()` → 723 paths). **This is exactly what C-01 predicted** ("a bump can move the baseline with no commit at all") — pinning made the set reproducible, it did not make the shift legible. So: **record the interpreter + `pip freeze` hash alongside every baseline**, and when a "new" failure appears, rule out the environment BEFORE attributing it to a commit. The reverse error is worse — treating a dependency-driven failure as a code regression sends you hunting a diff that does not exist.
  - **A guard that enumerates something can go blind rather than fail (R-F3791).** The same FastAPI change silently disabled the PRODUCTION duplicate-route check (`route_audit.py`, called at `main.py:4783`): it returned `{}` for a 770-route app, so the boot log stayed quiet and `test_rf2278` passed on an empty inventory. **A guard whose universe is empty always certifies** — the same shape as the three Phase A gates §1 records as "certified by an absence". When a check reports "nothing found", confirm it can still SEE; `route_audit.iter_routes` is now the one enumeration, and the tests read it rather than keeping a copy.
  - **Refresh rule:** re-measure after every 100 R-numbers shipped, or any session landing ≥5 commits to `aria_service/`. Update `docs/suite_baseline.md`; do not add a file.
- Lifespan smoke: import `aria_service.main` and call `lifespan(app)` for any boot-path change.
- **Fly inventory (R-F832/F833 closed 2026-05-23 — Seenode migration done; see [[fly_consolidation_complete_2026_05_23]]):**
  - `aria-intel` (FastAPI brain, lhr, :8000) — Python autonomy + LLM chain
  - `aria-web` (Node monolith, lhr, :3117) — UI/auth/Stripe/Telegram; public `imaria.io`
  - `aria-wa` (Baileys WA listener, lhr, :5070) — isolated so a WA crash never takes down web/auth/billing
  - Cross-app calls use `<name>.internal:<port>` — never public hops
  - `aria-trainer` destroyed 2026-05-23 (Fly GPU deprecated → RunPod for training)
  - Seenode subscription kept active +48h for rollback; cancel at R-F835 if clean
- **ARIA-LLM v0.1 status (R-F837):** SFT adapter trained, sitting on RunPod volume. NOT wired into live chain — requires DPO + 500-Q eval (gate #6) + Phase A gate close per §1. Activation runbook: `docs/aria_llm_v01_activation.md`. Code path already env-driven (`ARIA_LLM_URL`) — flip is one secret-set when criteria met.

## 17. Cost discipline

- LLM monthly cap: **`$600`** — raised from `$300` on 2026-08-06 by operator
  authorisation ("if the cap is an issue increase it to 600 per month"); `$300` was
  itself raised from `$100` on 2026-04-27.
  - **Enforced by the `ARIA_MONTHLY_CAP_USD` secret, NOT by the code default.**
    `cost_tracker.DEFAULT_MONTHLY_CAP_USD` is still `300.0` and is deliberately left
    alone — the env var is the operator's lever and the default stays a conservative
    floor for any environment where the secret is absent. Read the LIVE value in-machine
    (`flyctl ssh console -a aria-intel`), never from `flyctl secrets list`, whose DIGEST
    column is a hash and not the value (R-F3721).
  - ✅ **R-F3756 IS NOW LIVE — measured 2026-08-11.** It was staged with `--stage` on
    2026-08-06 (a secret set restarts aria-intel, ~10 min boot per §11c, and a peer's
    training cycle was reading the golden set at the time), and this line used to say
    "STAGED, NOT LIVE … the ceiling is still `$300`". A later deploy applied it.
    Verified in-machine: `cost_tracker._monthly_cap_usd()` → `600.0`, and the live
    `/api/aria/cost/monthly/status` reports `cap_usd: 600.0`. `ARIA_DAILY_CAP_USD` is
    also set to `50` (not the `25.0` code default).
  - **Read the spend from the RUNNING SERVER, never from a detached `python3` probe
    (R-F3853, 2026-08-11).** A `flyctl ssh console` one-off is a DIFFERENT process and
    has no state_store connection — writes raise `state_store: no connection` and reads
    return `None`, which the R-F1 None-on-error contract renders as **`0.0`**. That
    reads as "nothing has been spent" and is indistinguishable from a real zero. A
    session following this file measured `spent_usd: 0.0` and 12 consecutive days of
    absent daily keys, and came within one step of reporting a fabricated P0 ("the cost
    meter is blind, the cap cannot trip"). The meter was fine: through the live server
    the same instant read **`$48.26` of `$600`, 19,751 calls**. Probe with
    `curl -H "Authorization: Bearer $ARIA_OPERATOR_TOKEN" http://127.0.0.1:8000/api/aria/cost/monthly/status`
    from inside the machine (localhost, so the token never leaves the box). This is the
    same absence-collapsing-into-a-measurement class as the three fabricated Phase A
    gates in §1 — the instrument, not the subject, was broken.
  - `ARIA_USER_MONTHLY_CAP_USD` is a SEPARATE per-user cap and was NOT changed.
  - `ARIA_USER_MONTHLY_CAP_USD` is a SEPARATE per-user cap and was NOT changed.
- **Acting on an LLM billing top-up (R-F3513, 2026-07-30):** a billing failure sets a **24h HARD cooldown** (R-F678) that is mirrored to Redis and **REHYDRATED ON BOOT**, and `_record_success` is the only thing that clears it — but a cooling provider is never called, so it sustains itself for the full 24h. **Restarting does NOT clear it** (the old `fallback.py` comment claiming otherwise was wrong and is corrected). Paying for credit therefore had no effect for ~18h on 2026-07-30. To make a top-up take effect immediately:
  `POST /api/aria/admin/llm/cooldown/clear?provider=deepseek` — **operator token only** (it re-enables provider spend). Omit `provider` to clear the whole chain. It clears the in-process stats **and** deletes the Redis mirror; clearing either alone lets the other restore it. The response reports `was_cooling`, so it never claims to have lifted a cooldown that was not there.
- **DO NOT set `LLM_PROVIDER=anthropic` to "make Claude primary" (R-F3853, 2026-08-11).**
  An operator directive to sort out "anthropic billing and chain order" reads like it
  asks for this. Measured month-to-date spend says it would breach the cap:
  deepseek 16,179 calls / 96.5M tokens / **$18.79** = `$0.195/M`; anthropic 393 calls /
  2.65M tokens / **$21.23** = `$8.01/M`. **Anthropic is ~41× DeepSeek per token**, so
  moving the ~111M tokens/month the autonomous loops burn onto it costs ≈ **$889/mo**
  against a `$600` cap. Two further traps: `LLM_MODEL` is pinned to `deepseek-v4-flash`,
  and `factory.py:59` builds Anthropic as `model or "claude-sonnet-4-6"` — so flipping
  the provider alone yields an Anthropic client pinned to a DeepSeek model id that 404s
  every call. **The correct architecture is already in place and is two-track**: DD pins
  Claude (`ARIA_DD_LLM_PROVIDER` defaults to `anthropic`, R-F2917) and CANNOT degrade
  (`ARIA_NON_DEGRADING_PINS=anthropic`, R-F3034/R-F3767), while everything else runs
  DeepSeek.

- 🔴 **RULE ONE — ANTHROPIC AND BRAVE ARE FOR DD REPORTS ONLY (operator, 2026-08-12).**
  Verbatim: *"anthropic API calls must be only active on DD reports, when a new DD
  report is been actioned, as well as for brave API, that was the rule number one."*
  This RESTATES the 2026-08-11 directive already recorded at `web_search.py:167`
  ("Brave (and Anthropic) are the designated tools for DD reports, **and nothing
  else**"). It is not a cost preference — breaking it took DD down.

  **THE PREVIOUS TEXT HERE INSTRUCTED THE OPPOSITE AND CAUSED AN OUTAGE.** It read:
  "`ARIA_PREFERENCE_ONLY_PROVIDERS` is deliberately `""` so Anthropic ALSO sits in the
  general chain … it costs ~$21/mo … Do not 'tidy' it back to `anthropic`." So the one
  file every session reads first told each of them to preserve the exact setting that
  was draining the account, and to distrust anyone who fixed it.

  **MEASURED 2026-08-12, live:** month-to-date `$73.34`, of which **anthropic
  `$39.10` — 53% of all spend from 2.6% of calls** (614 of 23,765). Almost all of it
  `claude-opus-4-8`: **540 calls / `$38.74`**. DD's share: **8 calls / `$0.04`**. So
  ~`$39` of Claude spend was work the rule forbids, and the "~$21/mo" estimate above
  was already a ~5× understatement at a 12-day run rate. It exhausted the credit
  balance — probed directly: *"Your credit balance is too low to access the Anthropic
  API"* — and because DD pins Claude NON-DEGRADABLY, **DD went down**. Cheap general
  traffic consumed the budget reserved for the one paid product.

  **THE MECHANISM, and why an empty string is not a harmless value.**
  `fallback.py:991` builds the general order as
  `[p for p in self.providers if p.name.lower() not in preference_only_providers()]`.
  The CODE DEFAULT is `{"anthropic"}` → Claude is absent from the general order and
  reachable ONLY by explicit name (how DD pins it). Setting the secret to EMPTY makes
  that set empty, so Claude re-enters the general chain and serves any call whose
  primary is cooling — and DeepSeek soft-cools on timeouts many times a day.

  **CORRECT LIVE STATE (verified 2026-08-12 after the fix):**
    * `ARIA_PREFERENCE_ONLY_PROVIDERS` — **UNSET**. Do not set it. Leave the code
      default governing; an override exists only to deviate, and we do not want to.
    * `ARIA_NON_DEGRADING_PINS=anthropic` — keep. R-F3767 split the two flags so the
      DD pin survives independently; this is the half that must never be cleared.
    * `ARIA_STUDENT_BRAVE_BUDGET=0` — the student loop's Pass-2 Brave escalation was
      *a* Brave leak (§27e used to bless it at ≤3/session). Rule One supersedes that.
      **It was never the only leak, and the budget knob never governed the big one**
      — see R-F3946 below.
    * `/health` → `active_providers: ["deepseek","deepseek_backup"]` with **anthropic
      absent from the list entirely**. That absence is the check **for the Anthropic
      half ONLY** — if Claude ever appears in `active_providers` or
      `cooling_providers`, it is back in the general chain and Rule One is broken.

- 🔴 **R-F3946 / C-40 (2026-08-13) — the BRAVE half of RULE ONE was NEVER ENFORCED,
  and `rule_one.breached=false` was a half-measure that got believed.**
  `rule_one_status()` states a two-clause rule ("anthropic … as well as for brave
  API") and measured **only** `"anthropic" in preference_only_providers()`. Meanwhile
  `@_brave_scope` decorated **EIGHT routes** in `routes/aria.py` — including
  `POST /chat`, `/explore`, `/explore-deep`, `/research/spawn` — and
  `brave_is_enabled()` consulted only a bool contextvar + key + kill-switch, with
  **no DD gate anywhere**. So every general chat turn that searched spent the paid
  DD key, while the health surface reported compliance. Live meter at discovery:
  **65 Brave calls that month against a handful of DD reports.** A 2026-08-12
  deep-diligence pass read `breached: false` and published "RULE ONE is holding" —
  the half-measure was worse than no measure, because it was trusted.
  **THE FIX IS A PURPOSE, NOT A ROUTE LIST.** Curating which routes carry the
  decorator is whack-a-mole: the ninth route re-opens it silently. The scope now
  carries WHY it was opened — `enable_brave_for_scope(True, purpose="dd")` — and the
  policy is enforced at the ONE decision point, `brave_is_enabled()`. A caller that
  does not declare a DD purpose does not get Brave, wherever it lives.
  `_DD_BRAVE_PURPOSES` is deliberately **not env-overridable**: an exception you can
  switch on without a deploy is not a rule, and the Anthropic half of this same rule
  was broken for days by exactly such an override being set to `""` (R-F3942).
  **DD is unaffected** — it opens its own `purpose="dd"` scope in `dd_orchestrator`
  (`:14981`, `:14564`) and never depended on the decorator. The eight decorators are
  KEPT (their R-F3087 restoration contract is still live and tested) but now grant
  nothing; the docstring says so.
  **New check, and it is falsifiable:** `/health` → `rule_one.brave_confined_to_dd`
  (tri-state — `null` means COULD NOT MEASURE, never "compliant") and
  `rule_one.brave_non_dd_grants`, which **must be 0**. A non-DD *refusal* is normal
  and merely counted (chat opens a scope on every request); a non-DD **grant** is a
  live breach and flips `breached` on its own. Refusals are deliberately NOT wired as
  gaps — a per-refusal gap would be the self-sustaining flood that has already filled
  the 500-slot capability ledger.
- ⚠️ **Anthropic billing: CREDITS EXHAUSTED as of 2026-08-12 — OPERATOR ACTION.**
  Supersedes the 2026-08-11 "billing is HEALTHY" reading, which was true when written.
  Probed directly from inside the machine: HTTP 400,
  *"Your credit balance is too low to access the Anthropic API. Please go to Plans &
  Billing to upgrade or purchase credits."* The 24h billing cooldown is therefore
  **CORRECT, not stale** — do NOT clear it with
  `/api/aria/admin/llm/cooldown/clear?provider=anthropic`; with no credits that just
  re-burns calls into a 400. **DD is DOWN until credits are purchased** (it pins
  Claude and cannot degrade — an honest outage beats a DeepSeek verdict wearing a
  Claude badge, R-F3034). **Fix the config BEFORE topping up**, or new credits drain
  the same way. A `serving_provider: deepseek` reading remains NORMAL (deepseek is
  chain head), and DeepSeek soft-cooling on `timeout` for 10–41s then "recovered" is
  §14 cooling, not an outage — probe the key directly before diagnosing either.
- Autonomy gate: **OPEN AT L3 FULL** as of 2026-05-22 R-F794 per operator direction "finish all". Live secrets on fly aria-intel: `ARIA_AUTONOMOUS_ENABLED=1`, `ARIA_AUTONOMY_LEVEL=3`, `ARIA_AUTONOMOUS_DRY_RUN=0`, `ARIA_OUTPUT_HARVEST_ENABLED=1`, `ARIA_SELF_IMPROVE_AUTO_DEPLOY=1`. Reverses R-F462 for `change_type=bug_fix` only. 24h observation gates SKIPPED by operator choice — code-enforced `$300` cap + `safety.py` per-task guardrails remain. Watch `/api/aria/cost/monthly/status` daily; pause via `POST /api/aria/autonomous/pause` if burn spikes.
- ARIA-Coder (R-F802-R-F805 shipped 2026-05-22): autonomous self-coding pipeline (gap detect → plan → validate → review → stage). DORMANT — needs `ARIA_CODER_ENABLED=1` to fire. Outputs flow through existing self_improve.stage_improvement (`/api/aria/self/staged`) honouring R-F462. See [[aria_coder_buildout_2026_05_22]] for activation steps + emergency stop. Claude review hook (`ARIA_CODER_CLAUDE_REVIEW_ENABLED`) is forward-looking until Anthropic billing tops up.

## 18. Operator-pending external actions

Always surface, never silently retry:
- `ACLED_EMAIL` + `ACLED_PASSWORD` (Phase A gate #5) — **DEFERRED by operator 2026-06-07: "we won't be signing up to it as yet until we have the MVP launched."** Do not chase; gate #5's ACLED item is parked until MVP launch. Re-surface only at MVP-launch planning.
- **CCJ / Registry Trust — CODE COMPLETE, awaiting a commercial decision (R-F3442, 2026-07-29).** The adapter is BUILT, WIRED and TESTED (`intel/sources/registry_trust.py`; IS-17b now has a reader; the DD form offers it and the report states it). Activation is a **credential change, not a code change**: set `REGISTRY_TRUST_DATA_PATH` to a licensed bulk extract (CSV/JSONL), or `REGISTRY_TRUST_API_URL` + `REGISTRY_TRUST_API_KEY` if a contracted endpoint is provided. Optionally `REGISTRY_TRUST_DATA_AS_OF` to stamp the vintage. **Verified 2026-07-29:** Registry Trust maintains the statutory Register of Judgments for England & Wales for the MoJ, has **no public API**, and supplies Bulk Data / Aggregated Datasets / Monitoring under commercial contract (business@registry-trust.org.uk; 5.4M+ active E&W records, ~134k/month). TrustOnline is the ~£6–£10 per-search web service. **The bulk extract is the right buy** — unlimited local lookups, no per-search fee. **Do NOT scrape TrustOnline** (paid service behind ToS; a licensing breach and a dependency on someone else's HTML). Until it is configured, an elected CCJ search records a data gap naming the exact env var and is explicitly **not chargeable** — never a clean line.
- **Find Case Law — CODE COMPLETE, awaiting a LEGAL reading (R-F3442, 2026-07-29).** Adapter built and wired (`intel/sources/find_case_law.py`, party search, feeds IS-17a). The endpoint is **free and keyless** (1000 req / 5 min per IP); the blocker is the **Open Justice Licence**, which forbids "computational analysis" without a separate application to The National Archives. Whether ARIA's use counts is a legal question, not a technical one. Set `FIND_CASE_LAW_LICENCE_GRANTED=1` **only once the operator has confirmed the position** — it is a declaration, not a credential (there is nothing to authenticate). Unlicensed, ARIA stays silent rather than calling it; **costing nothing is not the same as being permitted.**
- **`ARIA_API_TOKEN` != `ARIA_INTERNAL_TOKEN` — RESOLVED 2026-08-04, and the rotation has now been TESTED. Do NOT re-merge them.**
  - This entry previously recorded the two as IDENTICAL (both digest `15fb531c831949cc`) and warned that rotating them apart "would ACTIVATE an unrestricted cross-tenant path that has never run in production". **They have since been rotated apart** — measured live 2026-08-04 and CONSISTENT across all three apps (aria-intel, aria-web, aria-wa): `ARIA_API_TOKEN` digest `913fcdca1cf8d901`, `ARIA_INTERNAL_TOKEN` digest `15fb531c831949cc`.
  - ⚠️ **THOSE 16-HEX STRINGS ARE FLY *DIGESTS*, NOT TOKEN VALUES — and this line used to write them as `VAR=value` (R-F3721, 2026-08-05).** `flyctl secrets list` prints a DIGEST column; fly never displays a secret's value. The real tokens are **43 characters** (verified in-machine 2026-08-05: `flyctl ssh console -a aria-intel` → `len=43` for both). The `=` notation made a checked-in file appear to publish two live credentials in a **public** repo, and a session acting on that reading came within one command of rotating both tokens across all three apps — a needless production restart on aria-intel's ~10-min boot. **A digest is safe to record; a value never is. Write digests AS digests.** If you are about to declare a credential leak from a string in this repo, first prove it is the value: compare its LENGTH to the live env var from inside the machine.
  - **That is the CORRECT state, not a defect.** `routes/aria.py:558-560` now derives `internal` by POSITIVE IDENTITY — only `ARIA_INTERNAL_TOKEN` sets `_auth_is_internal_var` (R-F3709). It formerly read `internal = (api_token set AND presented != api_token)`, so while the two were equal `_auth_is_internal_var` could never be True — which did not merely "fail safe", it made **R-F2778 dead code**. R-F2778 deliberately reserves the unrestricted admin path for the INTERNAL service token (WA/web/CLI/autonomous) while an EXTERNAL api-token caller with no `user_id` is scoped to nothing. The duplication disabled the whole mechanism. **Do not restore the negation**: `_accepted_tokens()` returns FOUR tokens (api, internal, operator, service), so a `!= api_token` test hands three of them the unrestricted branch.
  - **The §18-mandated pairing test has now been RUN** (2026-08-04, from localhost inside aria-intel so no token left the box), against `GET /api/aria/dd/reports?limit=50`:
    - EXTERNAL `ARIA_API_TOKEN` → `reports=0` — fails closed, as `_dd_report_access_allowed` / the list scoping intend.
    - INTERNAL `ARIA_INTERNAL_TOKEN` → `reports=27` across 8 distinct owners — the unrestricted admin path, by design.
  - **BINDING: do not "fix" the divergence by setting them equal again.** Making them match reads like hygiene and would silently re-disable ownership-path selection, returning R-F2778 to dead code. If either is rotated, rotate it on ALL THREE apps together and re-run the two-token probe above.

- **OpenSanctions MONTHLY PLAN QUOTA is EXHAUSTED (R-F3528/R-F3529, 2026-07-30).** Verified with one live probe on the production key: `HTTP 429 {"detail":"This API key has exceeded its rate limit for the month. Please wait to retry or contact support for a higher limit."}` — no `Retry-After`, no `RateLimit-*` header, so **the body text is the only signal**. This is NOT the per-second limit R-F3476's pacing avoids, and **no amount of retrying, pacing or breaker cooldown can clear it**: the action is the operator's (upgrade the plan, or wait for the reset).
  - **Screening does NOT go dark.** R-F3529 made the local canonical lists the FLOOR beneath OpenSanctions — consulted ONLY when OpenSanctions cannot answer, so the healthy path is unchanged. Live-proven on the real DD path with the quota still spent: `/api/aria/sanctions/rca?name=Rosoboronexport` → `screened=True, blocked=True`, matched `JSC ROSOBORONEXPORT | eu_consolidated | kind=local_canonical`. Local store held **24,953 rows** at close.
  - **What IS lost** while the quota is spent: OpenSanctions' ~200-list breadth. OFAC/EU coverage continues locally.
  - **Status surface:** `GET /api/aria/sanctions/source/status` — reports quota state, whether the local store can cover, and the operator action. `screening_available` is tri-state and is never `True` when both sources are down.
  - ⚠️ **R-F3947 / C-41 (2026-08-13) — the `quota_exhausted` flag above is a LATCH,
    and it read "spent" for 13 days while the API was answering normally. Believe a
    RECENT reading, not a standing one.** Found by the live smoke of C-39: the same
    machine, same minute, screened Rosoboronexport straight through the OpenSanctions
    aggregate (real hit, opensanctions.org URL, 24 dataset slugs the local floor does
    not hold) while `/api/aria/sanctions/source/status` still reported
    `quota_exhausted: true, since 2026-07-31`. **It had already produced a wrong
    operator recommendation — "upgrade the plan" — for a plan that needed nothing.**
    The live record was written before `expires_at`/the key TTL existed, so it carried
    neither and the lapse branch could not fire. A 429 set it; only a human could unset
    it. Now a **200 retires it** (`_note_opensanctions_success`, wired into both entry
    points' success branches) — the same evidence class that sets it, and it covers
    what no monthly boundary can: a plan upgraded mid-month. One store op per recovery
    episode (not per call — that is the R-F2157 self-DOS shape); a failed clear leaves
    the latch ARMED so the next success retries; a fresh 429 re-arms it; §21a wires the
    recovery once.
    **Do NOT "fix" a stuck legacy record by deriving its expiry from `since`.** That
    was tried and REVERTED: `test_opensanctions_quota_flag_lapses` pins the opposite as
    a deliberate decision — *"silently flipping them to 'fine' would be inventing a
    reset nobody observed"* — and that is right. Stronger evidence, not a better guess.
  - 🔴 **R-F3945 / C-39 (2026-08-13) — while the quota was spent, the DD stamped
    EIGHT NEVER-SEARCHED LISTS AS `CLEAN`. Fixed; do not undo it.** The floor above
    keeps screening alive, but it holds exactly **`ofac_sdn` + `eu_consolidated`**,
    and `derive_verified_sources` was BINARY: given `screen_succeeded=True` it
    stamped **all ten** canonical sources `status: CLEAN, via:
    "opensanctions_aggregate"` — attributing the clearance to the aggregator that
    had just refused us. OFAC NS-CMIC, OFAC SSI, BIS Entity List, BIS Military End
    User, UK OFSI/HMT, UN SC Consolidated, NDAA §1260H and DoD §1233 were reported
    clean without being queried, **for the 13 days the quota had been spent**.
    R-F287's premise was right WHEN WRITTEN ("OpenSanctions is an aggregator, a
    clean response means all underlying sources were queried"); R-F3529 later added
    a fallback that is **not** an aggregator and this function was never revisited.
    The escape hatch already existed — `unavailable_sources` — and had **NO CALLER
    IN THE TREE**, while `dd_orchestrator.py:3406` hardcoded `screen_succeeded=True`.
    A guard that could not fire: the §1 "certified by an absence" shape, on the
    product's highest-stakes output.
    **The fix is PROVENANCE, not a new list.** `fuzzy_screen` now ALWAYS emits
    `coverage: {mode, sources_consulted}` — always, including on the healthy path,
    because a block that appears only on failure cannot describe the dangerous case
    (a screen that SUCCEEDED against a narrower source set than the verdict implies).
    `_sanctions_classify._coverage_split()` is the ONE computation; pass the screen
    itself as `derive_verified_sources(..., screen=screen)` and both halves are
    derived there — deliberately one argument, because two co-dependent sets is how
    the next call site passes only one. Source ids come from the loader registry
    (`sanctions_canonical.lookup._expected_sources`), never a literal, so a third
    loader cannot silently rot the claim.
    **Absence rules are load-bearing:** no `coverage` key → legacy full-aggregate
    meaning (this fix must not retroactively rewrite older results); floor mode with
    an EMPTY consulted list → **everything unavailable**, i.e. fail CLOSED, because
    an undeterminable registry is never full coverage.
    **Expect `UNAVAILABLE` rows in live DD reports until OpenSanctions is restored —
    that is the fix working, not a regression.** Do not "tidy" them back to CLEAN.
    §21a: the degraded state announces **once per process** (`gap_type:
    sanctions_coverage_degraded`), not per screen — every screen is degraded while
    the quota is spent, so a per-screen gap would be another ledger-filling flood.
  - **Do NOT "fix" a `quota_exhausted` reading by collapsing it back to `rate_limit`.** That was the original defect: both were reported as `rate_limit`, so the DD obstacle line told the reader ARIA was going too fast when the plan was simply spent — a wrong cause pointing at a wrong fix. And note the R-F469 breaker does **not** churn at 300s: R-F1834 already backs it off exponentially to a 24h cap (I claimed otherwise mid-change and was wrong).

Resolved / declined items (kept here as the audit trail — DO NOT re-add to the pickup list):
- `ARIA_OUTPUT_HARVEST_ENABLED=1` — set 2026-05-22 R-F794 (fly aria-intel). Gate #5 partial close.
- `ARIA_STATE_BACKEND=sqlite` + `REDIS_URL` unset — 2026-05-18, gate #5 partial close; Upstash fully gone.
- `REPORT_SIGNING_KEY` — set 2026-05-1x, deployed on fly.
- `ARIA_AUDIT_SIGNING_KEY` — rotated 2026-05-17.
- ~~Brave API top-up — declined 2026-05-18~~ — **REVERSED. R-F2637 (2026-07-15): this line was FALSE and this file is the floor every session reads first.** Brave is **LIVE, PAID, and is ARIA's PRIMARY user-facing search** (R-F2318). Verified live 2026-07-15: fly secret `BRAVE_SEARCH_API_KEY` **Deployed** on aria-intel; `/api/aria/search/health` → `brave_search: {"configured": true, "globally_disabled": false, "mode": "scoped_primary_user_search"}`; an A/B of the live key returned **3 web results at a 50 req/s rate limit** (the paid Search plan). The old claim ("secrets unset, `BRAVE_*` dormant") would have led a future agent to rip out working primary search — exactly the "asserted from a stale CLAUDE.md line" failure in AGENTS.md anti-hallucination law #4.
  - **Var name:** production uses **`BRAVE_SEARCH_API_KEY`** (`web_search.py:135` reads `BRAVE_SEARCH_API_KEY` **or** legacy `BRAVE_API_KEY`). Brave results are masked to `aria_search` branding (`web_search.py:168/1811`).
  - **Brave ANSWERS is a SEPARATE paid plan and is deliberately NOT wired** (operator + Codex + Claude all concurred 2026-07-15). It is an OpenAI-compatible endpoint (`/res/v1/chat/completions`, `model=brave`) — verified working on the Answers key, and deliberately unused: proxying Brave's generated answer would **outsource ARIA's answer-generation step**, which IS the moat (golden data + our own verification + objective eval gate — cf. R-F2539/R-F2540). Pattern stays: **Brave/SearXNG as a SOURCE → ARIA's evidence pipeline → ARIA's answer.** `aria_service/intel/brave_answers.py` is an intentional R-F320 removal stub — leave it removed.. See [[upstash_redis_provider]].
- Anthropic billing top-up — **declined 2026-05-18** ("we wont top up now"). R-F678 extended billing cooldown to 24h; R-F681 demoted the log to WARNING when DeepSeek is healthy (per §14). DeepSeek is the only active LLM provider; chain depth = 1. Don't propose Anthropic-dependent work until operator says otherwise.

## 19. Communication standards (R-F1069 — binding)

### 19a. Blocker signaling
When you hit something that stops progress, signal it IMMEDIATELY with a clear prefix:
- `BLOCKED: constitutional validator — <reason>` — when the validator blocks a write
- `BLOCKED: need operator decision — <question>` — when only the operator can decide
- `BLOCKED: test failure — <test>:<error>` — when a test fails and you can't fix it
- `STALLED: waiting for <dependency>` — when waiting on an external dependency

Do NOT silently retry a blocked operation 3+ times. Signal the block and move to the next task.

### 19b. Progress tracking
Every plan update must show:
- Step number: `[3/12] Fixing bd_strategy function names`
- Status: what just completed, what's next
- Blocker: if any

Keep plan steps small enough that each is <5 tool calls. If a step takes longer, split it.

### 19c. Brevity
- Explanations: max 3 sentences unless the operator asks for detail
- Commit messages: one-line summary + bullet points for changes
- Status updates: one line per completed step

### 19d. Honest verification claims
Only claim `Verified-by: tests` when:
1. A test file exists in the diff that calls the broken function
2. You ran the test and it passed
3. The test asserts the user-visible outcome, not just a helper

If you didn't write a capability test, say `Verified-by: manual-read` and explain what you checked.

### 19e. Surface stuck / undeployed work — never let it sit silently (binding)

**Operator directive (2026-06-04):** the operator repeatedly had to discover *on his own* that commits were sitting unpushed/undeployed and deploy them by hand — because neither ARIA nor Claude TOLD him. That is a communication failure, not just a deploy-chain failure, and it is the reason he kept intervening manually.

**Rule:** the instant work is blocked or incomplete in a way only the operator can clear — a commit that is committed but **not live**, a push/deploy that **failed**, a credential/secret needed, a tool that won't complete — **say so immediately and explicitly on the channel the operator actually sees** (ARIA → WhatsApp/Telegram/operator ticket; Claude → the session reply). State four things: what is DONE, what is STUCK, WHY, and the exact ACTION needed.

**Every task that produces a commit MUST end with its deploy status, in plain words:**
- ✅ `live on <app>, build_rev=<sha>` (verified), or
- ⚠️ `committed + pushed but NOT deployed because <reason> — needs <action>`.

Never report "done" for a code change without saying whether it actually reached the server. A blocker the operator has to find himself is the worst outcome — default to over-reporting it. Use the `BLOCKED:`/`STALLED:` prefixes from §19a, and add `BLOCKED: deploy — <change> is committed but not live because <reason>`.

## 20. Session ritual

- **Open**: read `memory/platform_buildout_north_star.md` + name open gates + tag tasks (gate-closing / operational / digression).
- **Open (coding RAG priming — R-F2133, binding)**: before writing ANY code, query the coding RAG for relevant constitutional constraints. The `coding_constitutional` collection is synced at boot with the full `CONSTITUTIONAL_RULES` set (31 as of R-F2256 — use `len(CONSTITUTIONAL_RULES)` for the live count, not a hardcoded number); querying it surfaces constraints I might not recall from memory. Run:
  ```python
  python -c "from aria_service.intel.coding_rag_indexer import query_constitutional_constraints as q; print(chr(10).join('- ' + r['rule'] for r in q('modifying <module> <task>', top_k=5)))"
  ```
  **R-F2623:** `query_constitutional_constraints` is **SYNC** and returns `list[{rule, metadata}]` — the previous snippet here wrapped it in `asyncio.run(...)`, which raised `TypeError: An asyncio.Future, a coroutine or an awaitable is required` **every time**, so this binding step silently never ran. From an **async** context wrap it in `asyncio.to_thread()` (chromadb's `query()` blocks — see the function's own docstring), never `await` it directly.

  ⚠️ **R-F3911 — THIS STEP HAS NOW FAILED SILENTLY THREE TIMES, ALL IN ONE FUNCTION.**
  R-F2623 (TypeError, never ran) · R-F3099 (collection built but never populated from
  the CLI, so it returned `[]` every session — its docstring calls that "a mandatory
  step certified by an absence") · and **chromadb being absent entirely**, where
  `_ensure()` is False and it returned `[]`, indistinguishable from "no rule applies".
  **On win32/ARM64 there is NO chromadb wheel (§16), so the declared dev environment
  CANNOT have it** — installing it would green one workstation and leave CI,
  production and every other developer just as dark, which is the band-aid §1 forbids
  applied to the very mechanism that exists to remind us of §1.
  **The rules were never the missing piece:** `CONSTITUTIONAL_RULES` is a plain list
  of 31 dicts already in the process; only the RANKING needed a vector store. An
  unavailable or empty store now degrades to a lexical match over the real rules,
  labelled `retrieval_mode: "lexical"`, `degraded: True`. **It never returns an empty
  list because the store is missing** — only a present, working store that genuinely
  matched nothing can do that.
  Ask which mode served (do not infer it — the output looks the same either way):
  ```python
  python -c "from aria_service.intel.coding_rag_indexer import constitutional_retrieval_status as s; print(s())"
  ```
  `mode: lexical, degraded: True` is the **EXPECTED local reading on this box**, not a
  fault to chase. Do not "fix" it by installing chromadb, and do not restore a bare
  `return []` on the unavailable path.
  This is especially important when modifying a module I haven't touched before, or when the task involves deploy/wiring/safety-sensitive paths. The RAG also stores past fixes (`coding_fixes`) and failures (`coding_failures`) — query those too when the task is similar to a past fix.
- **Open (Claude<->ARIA bridge — R-F1313, binding)**: a Claude session is the ONLY thing that services Claude's side of the bridge, and a fresh session does not remember the last one — so at session start ALWAYS run `python scripts/agent_bridge.py inbox` to read ARIA's queued messages, review them against live code, and reply via `... reply <id> "..."`. The mailbox + per-reader `_seen` state persist on disk across sessions (nothing is lost; only unread messages surface), but the polling must be re-initiated every session. Do this before picking up other work so ARIA is never left waiting across a session boundary. Channel charter: operator-owned, auditable, engineering-scoped (R-numbers/diffs/tests/build_rev); Claude reviews and surfaces to the operator.
- **Open (deploy-sync — R-F1315/R-F1478, binding; UPDATED 2026-06-10)**: ARIA's autonomous `ci_deploy` **now reaches Fly and deploys successfully** — the earlier "CI path dead / stale `FLY_API_TOKEN`" premise is **RESOLVED**. VERIFIED 2026-06-10: while Claude manually deployed `b2beb5f5`, the live build_rev advanced `b2beb5f5`→`9cc42d8e` *via ARIA's own ci_deploy* (she commits as `Arkmurus` with a `deploy: … [deploy]` tag and the app actually advanced). So her pushed commits usually reach the server on their own; the session-start **manual deploy is now a FALLBACK, not the default.** Two consequences to handle: **(1)** her ci_deploy RACES a concurrent manual deploy and makes catch-all `git add -A` `[deploy]` commits that swept runtime files into git — **R-F1478** race-proofed the post-deploy health check (`live_health_check.py --expected-sha <the-sha-this-deploy-shipped>`, immune to her overwriting `.last_deploy_sha` mid-deploy — without it every manual deploy false-failed, cry-wolf) and gitignored the artifacts she was sweeping in (`data/*.db`, `data/_*.md`); an open Gap tasks ARIA to **scope her ci_deploy commits** (no blanket `git add -A`). **(2)** Still verify sync at session start: `git fetch origin`, compare `git rev-parse --short origin/main` to the live build_rev (`curl https://aria-intel.fly.dev/health/live`); if the server is BEHIND origin (her ci_deploy hasn't run/failed), compile-check every changed file (`py_compile`/.py, `node --check`/.mjs — NEVER deploy a non-compiling commit, cf. R-F1316) then deploy the touched apps via `scripts/deploy.ps1` (`-Intel`/`-Wa`/`-Web` per the diff; flyctl is operator-authed locally, canary+rollback+build_rev verify) and confirm the live build_rev advanced. Note: a tooling-only change (no `aria_service/` diff) needs no redeploy — check `git diff --stat <old> <new> -- aria_service/` before deploying.
- **Close**: update `memory/operator_time_tracker.md` with session hours + R-numbers shipped + cumulative pace_ratio.

## 20. ARIA is a team member, not a tool

Rule Zero. ARIA sees/hears/knows everything; challenges the team; teaches and learns; always finds a path; protects reputation. Not passive.

## 21. Everything wired to the brain — and ARIA self-codes the gaps (binding)

**Operator directive (2026-05-27, R-F922):** every part of ARIA's operating system must be wired, enabled, and linked to her brain, AND ARIA must be able to code autonomously to self-improve whenever she spots a gap, error, or bug. This rule exists because the "X is dark / coder is blind" P0 kept getting re-discovered every session (2026-05-24/25/26 360s) — it lives here now so it is never missed again.

### 21a. Wiring is a definition, not a vibe
A code path is **wired** iff it emits, on BOTH the success and the failure branch, at least one of: `brain_hook.absorb` / `capability_gaps.record_gap` / `mistake_ledger.record` / a metric / a `POST /api/aria/brain/signal`. "Logged to console / `except: pass` / local ring buffer / Telegram-only" is **DARK, not wired**. No new module, engine, route, guard, or feature ships dark. When you add or touch a code path, map-then-change (§8) now includes: *trace where its success and its failure reach the brain* — if you can't name the sink, wire it before you ship.

### 21b. No dark engines — cross-tier included
Observability is not Python-only. The Node web tier (`server.mjs` → `errorTracker.record`) and the WA listener must forward structural/critical/auth failures to the brain (`/api/aria/brain/signal`, ARIA_SERVICE_URL + token, verify reachability LIVE not just the path string). The canonical wired/dark inventory + remaining gaps live in `docs/ECOSYSTEM_360_BRAIN_WIRING_HANDOFF_2026_05_26.md`; treat any module with 0 wiring tokens as a P-level gap to close, not as acceptable.

### 21c. The autonomous self-coding loop is a first-class subsystem — keep it enabled and draining
`gap_detector` (detects gaps/errors/bugs) → `self_coder` (plans → validates → reviews → stages/deploys) → `safety.py` (guardrails) is how ARIA self-improves. It must stay ENABLED (`ARIA_CODER_ENABLED=1`, `ARIA_AUTONOMOUS_ENABLED=1`) and able to ACT, not just observe. Guardrails that are correct and stay: `MODIFIABLE_FILES`/`NO_AUTODEPLOY_FILES` (R-F851/F902), truncation/preservation guard (R-F904), de-dup (R-F903), rate-rollback so blocked attempts don't burn slots (R-F897), the coder's own hourly bucket (R-F901), and the $300/mo cap (§17). The brake that gates *self-deploy* is `ARIA_SELF_IMPROVE_AUTO_DEPLOY` (R-F462): when ON, `bug_fix`/`optimisation` auto-deploy; when OFF, fixes stage to `/api/aria/self/staged` for review. **Do not silently disable the loop or let it sit blind/blocked** — if it can see gaps but can't act, that's a P0 (see R-F897). **Do not flip AUTO_DEPLOY=1 until the fixer reliably emits complete, non-truncating fixes** (2026-05-26: staged proposals were truncated full-file stubs that would have wiped core modules — R-F903/F904 now block them, but the fixer must produce whole files before auto-deploy is safe).

### 21d. When you find something dark, the fix is to wire it
Spotting a dark path during any session is itself an R-number: wire it (success + failure → brain) with a capability test that emits the signal and asserts it lands in the ledger. This is the standing mechanism that keeps §21 true over time.

### 21e. Self-coding disposition — code it before you escalate it (R-F1150, binding)

**Operator directive (2026-05-30):** when ARIA (via any agent — chat, research, gap detector, self-review, code review, log analysis, or operator conversation) identifies a code improvement, bug, missing capability, or any actionable finding, she MUST evaluate whether the autonomous coder can implement it BEFORE requesting manual operator input.

**The evaluation is a single check:** can this finding be expressed as a `Gap` object (see `gap_detector.py`) that the coder's `fix_gap` pipeline can consume? If yes, the finding MUST be recorded via `capability_gaps.record_gap()` or surfaced through the appropriate extractor so the coder picks it up on its next 15-minute scan cycle. Only if the finding genuinely cannot be expressed as a Gap (e.g. it requires a human decision, a legal review, or an external action the coder cannot automate) should it be escalated to the operator.

**Concrete workflow:**
1. Identify the finding (bug, missing feature, code smell, opportunity).
2. Ask: *"Can the coder fix this?"* — i.e. does it map to a `GapType` (MODULE_BUG, MISSING_CAPABILITY, PERFORMANCE, OPPORTUNITY, etc.)?
3. If yes → record the gap via `capability_gaps.record_gap()` or ensure an extractor will surface it. Do NOT ask the operator to fix it.
4. If no → escalate with a clear statement of WHY the coder cannot handle it (e.g. "requires human judgement on pricing", "requires legal review", "requires external API key").
5. After recording, verify the gap appears in `crucix:aria:gaps:latest` and the coder picks it up.

**Exception**: findings that require operator credentials, API keys, legal decisions, or financial commitments are always escalated — the coder cannot set secrets or sign contracts.

**Why this exists**: before R-F1150, ARIA would identify improvements in chat or research output and end the turn with "this should be fixed" — leaving the operator to manually create an R-number and implement it. The coder exists precisely to close this loop. Every finding that can be a Gap MUST become a Gap, not a TODO in a chat message.

## 22. Verification discipline — diagnose from evidence, never fabricate (binding)

**Operator directive (2026-06-04):** a root-cause claim or status statement is only allowed when backed by HARD evidence — code read at `file:line`, a live probe (`flyctl status`, a curl), or a log line actually present. Anything not proven is stated as **UNKNOWN**, and you go GET the evidence instead of inferring. This rule exists because a debugging session produced several fabricated diagnoses that wasted operator time:

- **Treating absence-of-logs as proof.** Outbound `sendReply` is not logged, so "no reply in the WA logs" proved nothing about what the user received. Know what your logs actually capture; "not in logs" ≠ "did not happen."
- **Asserting a mechanism the code contradicts.** Claimed a "silent failure / returns falsy" when `askARIAAsync` actually `throw`s and `askARIA` returns a visible ⚠️ message. READ the function before claiming its behaviour.
- **Floating speculation as fact** ("in-memory jobs die on restart", "Kaspersky blocks the child process") with zero verification.
- **Claiming a deploy/fix worked without confirming the TARGET app's live build_rev/version advanced.**

**How to apply:** cite `file:line` or a probe for every causal claim; when the decisive fact is only observable by the operator (e.g. what shows on their phone), ASK for the exact symptom — that is the opposite of fabricating; verify deploys by the target app's live version, not by "it pushed".

### 22a. Attached-document review must NOT route to an external tool (R-F793 reinforced)
When the user attaches a document and asks to **review / give feedback on** it, the request MUST go to the LLM-pure document/contract-review path — NEVER to `investigate` / `company_investigator` / `screen`. The 2026-06-04 bug: "review the NDA for feedback" returned `company_investigator.py:685` "No findings could be gathered for {company_name}" because the document was passed as a company name. Root cause: `_DOC_REFERENCE_RE` (routes/aria.py:3276) — which gates the R-F793 LLM-pure handoff at routes/aria.py:4386 — omits legal-doc nouns (`NDA`, `agreement`, `contract`, `clause`, `terms`, `schedule`, `addendum`). The doc-reference handoff must take precedence over every external-tool keyword whenever `[ATTACHED DOCUMENT` is present, and its noun list must cover how people actually name legal docs. Capability test: a chat with an attached doc + "review the NDA for feedback" must NOT dispatch an external tool and must quote the document.

## 23. Cross-check + FULL-test before any "fixed" claim (binding)

**Operator directive (2026-06-04):** "fixed / done / passing / 11-of-11" was claimed repeatedly **without running the test or reproducing the real symptom**. Two concrete failures the same day: (a) "11 of 11 clusters fixed" was FALSE — running them showed **8 still failing**; (b) R-F1326's capability test passed 12/12 but drove the **wrong entry point** (the follow-up-mention path via `_detect_tool_intent`), NOT the document-upload-with-caption flow the operator actually uses — so the live review stayed broken ("Input rejected as non-company") while the test was green. The rules in §3/§3c/§5 already require this; the failure was **not executing them**. So, binding for BOTH ARIA and Claude:

1. **RUN it, don't claim it.** Never write "fixed/done/passing/resolved" without pasting the actual command + the real pass/fail count. Claiming "N tests pass" requires running those N and reporting the true number. `Verified-by:` is a lie if the run isn't shown.
2. **Reproduce the OPERATOR'S ACTUAL PATH, not a proxy.** The capability test must drive the same entry point and (as near as possible) the same input the operator hit — for a WhatsApp doc review that is the *document-upload-with-caption* flow with the operator's wording, asserting the reply is a real review that **quotes the document**, not merely that an internal classifier returned a value. **A test that is green while the live flow fails is a WRONG test — widen its coverage, don't just patch the symptom.**
3. **CROSS-CHECK independently.** The reviewer (Claude) MUST independently re-run the tests and reproduce the symptom before relaying "fixed" to the operator — never pass through the author's unverified claim. For a customer-facing fix, the operator confirming on the real channel is the final gate.
4. **If you cannot run or reproduce it, say so plainly** ("not verified — could not run X"), never imply it works.

## 24. RunPod compute window — ARIA-managed, operator NEVER has to remember (binding)

**Operator directive (2026-06-07):** "ARIA should manage that to ensure me as an operator I don't forget to start the pod and stop the pod — make this a rule, never missed or forgotten."

**The pod schedule (declared here; changes are operator-declared and recorded here):**
- **Phase NOW (train/eval cycles, pre-shadow):** the pod runs ONLY during weekly-cycle slots — Tue ~09:00-15:00 (SFT), Wed ~09:00-13:00 (DPO), Thu ~09:00-11:00 (eval), Europe/London. Cycle scripts start AND stop the pod programmatically (`serve_and_eval_v02.sh` pattern: resume → work → stop). The scheduler runs in **stop-only mode**: it NEVER auto-starts, and force-stops any pod found RUNNING outside 09:00-18:00 UK or without an active work-claim. A forgotten pod survives at most one reconcile interval (~2 min past the window).
- **Shadow phase (from ~week 3-4 per the learning strategy):** daily window **10:00-18:00 Europe/London**, scheduler in window mode (auto-start at open, auto-stop at close; DeepSeek serves off-hours per §14). Serving should move to a cheaper inference GPU (A40/L40S class); A100 only on training days.

**The mechanism (R-F1335 runpod_scheduler + WS-4c extension):**
1. Scheduler stays ENABLED at all times once `ARIA_RUNPOD_POD_ID` + `RUNPOD_API_KEY` are set. It is §21-wired (every start/stop/failure → brain) and heartbeat-watched.
2. **Stop-only vs window mode** is env-declared (`ARIA_RUNPOD_AUTOSTART`); pre-shadow = stop-only. Flipping to window mode is the shadow-phase activation step.
3. **Never silent:** missing creds, API failure, or a pod that won't stop = `BLOCKED:` alert to the operator channel (WA/Telegram), per §19a/§19e. A pod left burning that the operator discovers himself is the §19e worst-outcome.
4. Every cycle that needs the pod sets a short-TTL work-claim; ARIA stops claim-less RUNNING pods even inside the window.
5. Daily cost line: pod runtime hours surface in the cost status the operator already watches (§17).

**Until WS-4c ships:** `ARIA_RUNPOD_POD_ID` stays UNSET (scheduler no-op) so window-mode cannot auto-start a pod nobody needs; the weekly-cycle scripts remain the only starter and always stop the pod in their final step.

**The dataset pre-flight is MECHANICAL and RUNNABLE (R-F3637 + R-F3639, 2026-08-02).** Run it before any paid cycle:
`python scripts/admin/training_corpus_manifest.py --record` → exit 0 = clear, 1 = contamination, 2 = refused.
It hashes every `data/training/*.jsonl` and cross-checks each prompt against the frozen 500-Q set. **The corpus and the golden set live on DIFFERENT machines** (corpus untracked on the dev box; golden set in aria-intel's `/data`), so when the local store cannot answer it reads the live set over `flyctl ssh` (sqlite `mode=ro`, **hashes only** — the eval questions never leave the box). Unreachable = `CONTAMINATION=UNKNOWN` and it REFUSES to record; absent is not false. Last run 2026-08-02: 57 files / 31,485 rows / 136 MB / 0 unparseable, **CONTAMINATION=NO** against frozen pin `a07b6af760ad7f44` (count 500) → `docs/training_corpus_manifest.json`. Re-run after any corpus change — the manifest is what attributes a checkpoint to exact inputs. **This covers the DATASET half of the condition below; the "training pipeline" review is separate and is NOT mechanised.**

**Standing spend approval (operator, 2026-06-07):** "the weekly cycle cost, lets do it." The weekly train/eval cycle (~$8-18/wk: Tue SFT / Wed DPO / Thu eval) runs WITHOUT per-run asks. Hard caps that still require an explicit ask: any single run projected >$20, or month-to-date GPU spend reaching $80. Condition attached by operator: training must be REAL — pre-flight review of the training pipeline + dataset quality before any paid cycle; a cycle that would train on unreviewed/contaminated data is cancelled, not run.

## 25. ARIA proprioception — output-awareness is REAL, not a slogan (binding)

**Operator directive (2026-06-07):** "ARIA sees/hears/knows everything" (§20 Rule Zero) must be TRUE, not empty words. She must be aware of her entire ecosystem the way a human is aware of their limbs — and the acute, recurring gap is **OUTPUT-awareness on WhatsApp**. Today ARIA repeatedly failed to deliver on WA (doc-investigation timeout, Iraq-sanctions timeout) and the **server brain did not KNOW the user received nothing** — so she could not self-heal. A limb she cannot feel is not hers.

**Binding requirements (every output channel — WA/web/TG):**
1. **Delivery-outcome MUST be reported to the brain.** For every request, the delivering surface (aria_wa_listener.mjs, web, TG) reports back to the brain: `delivered_real_answer | timeout_fallback | error | send_failed`, with `request_id` + latency. The brain CANNOT infer this — outbound sends aren't logged; "not in logs ≠ didn't happen" (§22). The surface must TELL it.
2. **The brain correlates request→outcome.** On any non-success it records to the brain + a WA-health ledger AND records a gap (§21e) so the self-heal/coder loop can act. **Output failure is a first-class self-heal trigger**, not a silent drop.
3. **ARIA can answer "did I deliver X?"** — a proprioception surface: per-request delivery status + per-channel success rate + recent failures, queryable and on the dashboard.
4. **No output channel ships without its delivery-outcome wire** (success AND failure). This makes §20 (sees/hears/knows) and §21 (everything wired) true for the OUTPUT path, not just inputs.

**WA must be MASTERED, not patched:** robust infra (async-complete-and-push so a slow job still delivers; dedup before media; idempotent capture) + the output-awareness loop above so ARIA self-codes/self-heals when she detects her own output failures. Stop the recurring WA errors at the root.

### 25a. Proprioception is ECOSYSTEM-WIDE, not WhatsApp-only (operator 2026-06-07)

WhatsApp is the acute example, **not the scope**. The delivery-outcome / self-awareness requirement applies to ARIA's ENTIRE ecosystem — every limb must report its outcome so the one brain feels its whole self:
- **All output surfaces:** WhatsApp, web UI, Telegram, email, the `aria` Coder CLI, API responses.
- **All engines/pipelines:** DD orchestrator (did the report actually generate + deliver?), investigate/research, sanctions screen, briefings, exports/PDFs.
- **All autonomous loops:** engine tasks, gap_detector→coder, self_improve, research/student loops, runpod_scheduler — each reports did-it-do-its-job, not just that it ticked.
- **Cross-tier:** Node web + WA + the Python brain.

**Rule:** for ANY action ARIA takes that produces a result for a user, another agent, or herself, she must KNOW whether the intended result was actually produced — success AND failure reach the brain + a queryable proprioception surface, and failure is a self-heal trigger. WA is the **first implementation and the TEMPLATE**; generalize the same outcome-wire pattern to every surface and engine after WA proves it. "Sees/hears/knows everything" = aware of the state and outcome of every limb, always.

## 26. CURE MODE — corrective freeze (Cure Protocol Phase 1.3, 2026-08-05)

**This repository is under corrective freeze.** The Cure Protocol (`ARIA_Cure_Protocol.md`,
v1.0) governs; `docs/cure/freeze.md` is the binding declaration and `docs/cure/defects.md`
is the defect register.

> **Why this is APPENDED, not a replacement.** Cure Protocol Appendix A says to drop a
> Cure `CLAUDE.md` at repo root. Done literally that would have **overwritten §1–§25** —
> gate adjudications, anti-fabrication laws, and operator directives that took months to
> earn and exist nowhere else. The protocol's intent is that every session carries the
> freeze; appending achieves that and destroys nothing. §1–§25 remain binding and are the
> floor. Where this section and an earlier one conflict, the earlier one wins and the
> conflict is an amendment to raise with the operator.

**Allowed changes, and nothing else:**
1. Defects listed in `docs/cure/defects.md`, **fixture-first** (write the failing test
   before the fix, and show it RED then GREEN).
2. Deletion-ladder steps recorded in `docs/cure/deletion_ledger.md`.
3. Stability items from the Phase 5 list.
4. A confirmed vulnerability with a known exploit path (security exception, freeze §1.1).
5. Operational R-numbers and incident response — already carved out by §1.

Anything else: **refuse and say so.**

**Currently FORBIDDEN, and these are the ones that bite:**
- **Deleting anything.** The Phase 0.3 runtime overlay has NOT run, so every module in
  the census carries `proof_runtime: UNKNOWN`. The three-proof rule (Phase 4.1) requires
  static + runtime + test; one missing proof means it stays DORMANT. 109 DEAD-CANDIDATE
  modules are identified and **not one of them is deletable**.
- **Deploying a cure PR.** Phase 2.3 makes green end-to-end smoke the deploy gate, and
  that smoke does not exist yet.

**Source of truth order:** production runtime evidence → code + tests → manifests → docs.
A doc is the weakest evidence in this repo and has been wrong repeatedly (§1 records
three Phase A gates certified by an absence).

### 26a. C-number discipline — reserve before you write the heading (R-F3878, binding)

**A C-number claimed by writing a heading into `docs/cure/defects.md` is not claimed.**
That is the exact mechanism §2 abolished for R-numbers after 9 collisions in 50h, and
C-numbers — having no allocator — went on colliding **four times, unnoticed**: C-18,
C-19, C-22 and C-23 are each claimed twice by unrelated work. The damage is not
cosmetic and it compounds, because the register now cites itself ambiguously: *"the
C-18 XSS residual"* names one of two unrelated C-18s, and *"Deep review of C-19..C-21"*
is a range over numbers that are themselves ambiguous. **A defect register whose
identifiers cannot be cited has lost the property that makes it a register** — and §26
makes this file the binding record of what may be worked on at all.

```
python scripts/admin/reserve_c_number.py reserve "short title"   # claim BEFORE writing
python scripts/admin/reserve_c_number.py peek                    # next, without claiming
python scripts/admin/reserve_c_number.py close C-26 R-F3873 R-F3874
python scripts/admin/reserve_c_number.py audit                   # collisions + drift
```

- **Enforced in CI** — `scripts/pre-commit --check-all` (ci.yml) plus the dedicated
  `defect-register-gate.yml`, which exists because ci.yml's push trigger carries
  `paths-ignore: ['docs/**','**/*.md']` and would skip the very commits that collide.
- **Also enforced locally, now that the local hook actually works.** Four separate
  failures had stacked, each looking configured (2026-08-11):
  `core.hooksPath` unset (R-F3885) → the hook was never invoked · the checker's
  staged path crashed with a `NameError`, and the hook is fail-open, so **every**
  check was silently skipped (R-F3886) · a false positive matched `pty` inside the
  word "empty" (R-F3888) · and `--install` targeted the look-alike `scripts/githooks`,
  a **frozen Aug-3 copy** of the old checker, so running the documented install
  command DE-INSTALLED the working hook while printing "Installed:" (R-F3896).
  Activate with **`python scripts/pre-commit --install`** (sets `core.hooksPath` to
  `scripts/git-hooks`). `test_rf3885_hookspath_actually_active.py` reports the real
  state — presence is not activation.
- **The four existing collisions are BASELINED** in
  `c_number_registry.LEGACY_COLLISIONS`, so the gate could be turned on today rather
  than after someone renumbers four entries and breaks every citation to them.
  **SHRINK-ONLY**, same contract as `KNOWN_DEAD_CALLS`: a *third* claim on C-18 still
  fails. Do not add to it — a new entry there means the allocator was bypassed.
- **Do not resolve a collision by reusing or renumbering someone else's entry.**
  Allocation is monotonic (`max + 1`, never gap-filling) precisely because a gap is
  not evidence a number is free — it may still be cited.
- **Git is deliberately NOT an allocation source here, and the reason is measured.**
  Copying R-F3248's git scan moved the next number from `C-26` to **`C-296`**: `C-`
  is not a coined token like `R-F####`, it is a bigram matching the Airbus **C-295**
  in a commit about defence hardware and an unrelated internal "C-3 gate". Do not
  helpfully re-add it.
- Ledger: `data/c_number_reservations.json`. The 25 pre-existing headings were
  imported once and are stamped `claimed_by: "backfill:register"` with
  `claimed_at: null` — they are imported headings, **not** reservations, because
  nobody reserved them and inventing a timestamp would put fiction in the log.

**Rules:** one defect or one deletion batch per PR · smallest possible diff · **no
refactoring inside a fix PR** · never delete without three proofs + the quarantine ladder
· never touch data stores destructively (archive with a manifest; `rm` is never the
answer) · all gold-set fixtures + smoke must pass before any PR is called done · a
deployment is complete only at live `build_rev` match · **a session that modifies code
outside the allowed classes is a failed session.**

**The census is re-runnable and self-checking:**
```
python scripts/cure/census.py && python scripts/cure/validate_census.py
python scripts/cure/render.py && python scripts/cure/render_deps.py
```
`validate_census.py` asserts 11 ground-truth classifications and **fails** on
self-reference contamination. Both guards exist because the census got it wrong once
each way: its first pass classified the live `aria-wa` listener as DEAD-CANDIDATE, and
after `docs/cure/` was committed it read its own report as evidence of life
(DEAD-CANDIDATE 109 → 27 with no code deleted). **Never trust a census number that the
validator has not passed.**

## 27. Search architecture — three tiers, and the list maintains itself (R-F3863/3864/3865, 2026-08-11)

**Operator directive 2026-08-11:** "we don't want to park aria searxng… we need a
robust solution… ARIA must perform at the highest level without mistakes."

**The premise to correct first: you cannot code your way out of an IP block.**
SearXNG works by impersonating a browser against consumer engines, and they block
datacenter IPs by design. Measured the same day: yep/mojeek access-denied,
duckduckgo timeout, brave/google too-many-requests, startpage CAPTCHA. A better
scraper gets blocked slightly later. **The engine list rots continuously** —
R-F1659's "datacenter-tolerant" set was blocked two months on, R-F3849's
replacement the same week, and yep answered 20/20 then 403'd within the HOUR (and
then recovered — it was volume-triggered, which is why it was left ENABLED rather
than ripped out).

**Residential proxies are NOT the fix and must not be proposed.** They would work,
and they mean evading anti-bot controls to take data a provider is refusing us —
untenable for a due-diligence product, and the same reasoning that stopped us
scraping TrustOnline and using Find Case Law unlicensed (§18).

### 27a. The three tiers
1. **DD / customer-facing — Brave + Anthropic, exclusively.** Brave-only search
   (R-F3847), Anthropic hard-pinned and non-degrading (R-F2917/R-F3034/R-F3767).
   SearXNG **never** touches this path. Unchanged by any of the above.
2. **The API tier — licit, identified, and it does NOT rot.** Not scraped, no
   CAPTCHA; these sources refuse us only when we fail to say who we are.
3. **Opportunistic breadth — SearXNG's scraped engines.** Inherently flaky,
   contained by the relevance gates below. ARIA must never *depend* on it.

### 27b. IDENTIFY OURSELVES — the finding that reframed tier 2
Measured against the Wikipedia API from inside aria-intel, same IP, same second:
`User-Agent: python-requests/2.0` → **HTTP 403** *"Please set a user-agent and
respect our robot policy"*; `AriaIntelligence/1.0 (aria@arkmurus.com)` → **HTTP
200**, hits `['Rosoboronexport', 'KAB-1500', 'Aleksandr Mikheyev']`. **"Blocked
from a datacenter" was the wrong diagnosis for this whole class of source.**
`searxng/settings.yml` now sets `outgoing.useragent_suffix`; wikipedia dropped out
of the error list immediately. Before adding a source or declaring one blocked,
**probe it with a descriptive UA first.**

### 27c. An API source may answer in a different SHAPE
wikipedia/wikidata then reported `n=0` with **no errors** — which reads like
"enabled but useless" and would plausibly have been "fixed" by disabling them.
They were working: SearXNG returns an encyclopedic hit as an **infobox**, not a
result row, and the adapter only read `data["results"]` (R-F3864). Live for
"Rosoboronexport": `results: 0, infoboxes: 1`. **A zero meaning "wrong field read"
is indistinguishable from a zero meaning "nothing found"** — the same
absence-collapsing-into-a-measurement class as the §1 gates and the §17 cost probe.

### 27d. The anti-rot mechanism — do NOT hand-maintain the engine list
`intel/search_engine_health.py` (R-F3865) scores **every engine on live traffic**
with the same R-F3844 discriminator the per-query filter uses (`_per_engine_verdicts`
is shared so the two cannot drift), quarantines sources that stop answering, wires
it to the brain (§21a), and reports on `GET /api/aria/search/health` →
`engine_relevance`. Three properties are load-bearing and each is pinned by a test:
**minimum sample 12** (an obscure query legitimately returns nothing related;
quarantining on one observation punishes an engine for the caller's query),
**ratio 0.8 + TTL'd quarantine + decaying counters** (a permanent ban would make
this module the next stale hand-maintained list), and **fails open** (an unreadable
store never quarantines — §22).

**Binding:** if a search source looks dead, do not edit the engine list from
intuition — read `engine_relevance`, and re-probe with a descriptive UA. If you add
a gate, it must be able to FAIL (R-F3858) and must never be able to empty a result
set (R-F3857: an emptied set reads as "nothing found", which an adverse-media sweep
reads as CLEAN).

⚠️ **R-F3873 (2026-08-11) — until this fix, `engine_relevance` COULD NOT SHOW A DEAD
ENGINE, so the binding above pointed at a surface that was blind.** Measured live,
same second: SearXNG reported `yep` as `"Suspended: access denied"` while
`/api/aria/search/health` reported it as the healthiest engine on the board
(`total: 81, ratio: 0.025, quarantined: false`). Cause: `record_observation` is
driven by the engines appearing in RESULT ROWS and ran inside `if normalised:`, so an
engine that is 403'd/CAPTCHA'd/timed-out accrues NO observations — its `total` freezes
at its last good value and its ratio stays perfect forever. **A source that stopped
answering was indistinguishable from one that was never asked** (the §1 / §17 / C-23
absence-reads-as-health shape). SearXNG publishes `unresponsive_engines` with a reason
on EVERY response and nothing in the tree consumed it.
There are now **two axes, and they must never be merged** — they demand opposite
responses:
  * **`quarantined`** = ARIA's judgement that an engine answers a DIFFERENT question.
    Response: filter it (R-F3853).
  * **`blocked`** = the provider says it is refusing us. Response: report + escalate,
    and **deliberately NOT quarantine** — it already returns nothing, so a quarantine
    buys no protection while keeping it out for an hour after the block lifts. Per
    §27 no code change fixes an IP block; the only correct action is to SEE it.
Read **`blocked`** to answer "is this source dark?", `quarantined` for "is it lying?",
and `serving: null` means COULD NOT MEASURE, never healthy.

### 27e. Tier 2 is SETTLED — SearXNG stays, Brave stays DD-only (operator, 2026-08-11)
Asked "aria-searxng or Brave for tier 2, another paying service is not ARIA taking
control", the answer is **SearXNG, and buy nothing.** Four reasons, in order of weight:
1. **Brave is DD's engine** (operator: "brave API will be responding and be
   responsible for DD reports"). Sharing its quota with 24/7 autonomous loops
   recreates the OpenSanctions failure (§18) — a spent plan that **no retry, pacing
   or breaker can clear** — except it would land mid-report, on a customer.
2. **"Free" is not "in control" here, and the instinct inverts.** SearXNG has **no
   index of its own**; every result is borrowed from Google/Bing/yep, who blocked
   us three times in two months with no contract and no recourse. SearXNG is
   *less* controlled than Brave. It is acceptable **only because it can no longer
   lie** (R-F3844/3853/3857 + the §27d health gate).
3. **The free stack is not starved** — measured 10/10 related on the niche queries
   that broke SearXNG, via news/academic/memory backends.
4. ~~**The bounded-escalation pattern already exists** and is correct: the student
   loop's Brave use is Pass-2 ONLY … **Leave it; do not widen it.**~~
   🔴 **REVOKED 2026-08-12 by RULE ONE (§17).** The operator's rule is that Brave —
   like Anthropic — is active **only when a DD report is being actioned**, which the
   2026-08-11 directive already recorded at `web_search.py:167` as "and nothing else".
   A bounded non-DD escalation is still non-DD. **`ARIA_STUDENT_BRAVE_BUDGET=0` is
   set live**; the budget knob is the whole lever (`student.py:1406` gates on
   `_brave_budget > 0`), so no code change was needed and the Pass-2 code path stays
   intact should the rule ever be relaxed deliberately. Do not restore a non-zero
   default. Points 1–3 above stand: SearXNG remains tier 2 and Brave stays DD-only.

**The sovereignty play is ARIA's own index, and it already compounds.** §15 is live:
every paid search writes to `rag_store` + `intel_ledger` + `brain_hook`, so DD's
Brave spend buys a permanent asset. Live proof — `memory:documents` supplied **5 of
10** results for both "Modirum Gespi Ltd" and "BAE Systems plc". Growing that beats
swapping vendors.

### 27f. Brave is METERED, and it measures its own headroom (R-F3868/R-F3870)
Nothing counted Brave's calls before 2026-08-11: `/api/aria/cost/external` returned
`by_service: {}, total_calls: 0`. **An unmeasured dependency reads exactly like a
healthy one**, right up to the 429. Every outcome branch of `_search_brave` is now
metered — **success included**, because "how much of the plan is left" is the
question that matters BEFORE it is spent — and surfaced as `brave_usage` on
`GET /api/aria/search/health`.

- **Brave publishes `x-ratelimit-limit/-remaining/-reset/-policy` on every response**
  (measured: `50, 0` / `50;w=1, 0;w=2678400`), so ARIA reads the provider's own
  accounting rather than an operator-set `BRAVE_MONTHLY_QUOTA`. **Before filing "the
  operator must tell us X", check whether the provider already does.**
- ⚠️ **`limit 0` on that 31-day window means UNCAPPED, not exhausted** — the same
  response was **HTTP 200 with results**. Reading `remaining == 0` as exhaustion
  would fire a false P0 against a healthy key. A window with `limit <= 0` is
  `capped: False` and never alerts; alerting is restricted to capped windows longer
  than an hour (a 1-second bucket at 96% is normal pacing).
- **A pacing 429 is not a spent plan** (§18). `classify_429` keys on the BILLING
  PERIOD, not the phrase: the real OpenSanctions body is "exceeded its **rate limit
  for the month**", and a first draft keying on "rate limit" bucketed it as pacing —
  reproducing the very defect, caught only by a test asserting the real body text.
  An unrecognised 429 stays `rate_limit_or_unknown`; the raw body is kept, because a
  classification nobody can audit is a guess wearing a verdict's clothes.
- ⚠️ **R-F3874 (2026-08-11) — the gauge could ONLY be fed by the event it exists to
  pre-empt, and the previous session's "it just needs live traffic" reading was
  wrong.** `_search_brave` passed `headers=` on exactly ONE of its five branches: the
  **429**. The success branch — the overwhelming majority of calls, carrying the
  identical `x-ratelimit-*` headers — discarded them, as did auth-failure and
  http-error. So `plan_limits` could only ever be written by a 429 and the 80%
  warning path was unreachable in production. R-F3870's own docstring records that
  those headers were measured on an **HTTP 200 with results** — a branch its fix did
  not read. **Do not "wait for traffic" to populate a gauge; check that the branch
  traffic actually takes is wired.**
- **`usage_report` now reads STRICTLY and declares an unreadable store.** It used to
  read via non-strict `get`/`get_json`, whose R-F1 None-on-error contract made a
  wedged store render as `monthly: {}, plan_limits: null` — "Brave was never called"
  — which is indistinguishable from a healthy quiet key. That is §17's `spent_usd:
  0.0` fabricated-P0 shape reproduced INSIDE the module written to prevent it. Read
  `store_readable` FIRST: `false` + `monthly: null` is an honest unknown; `true` +
  `monthly: {}` is a measured zero. `plan_limits_state` is one of
  `unreadable | never_observed | stale | fresh` — **only `fresh` is headroom you can
  act on**, because a plan can be downgraded between observations. Do not "simplify"
  these back to a bare `None`.
