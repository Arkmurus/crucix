# Incident 2026-06-28 — aria-intel prolonged outage: 10-minute boot + WAL bloat

**Status at writeup:** aria-intel is UP and stable — `build_rev=36d45c6c`, `/health/live` 200 (3/3),
machine v2167, 1/1 passing. All fixes below marked "live" are deployed. **Nothing in this doc is
deployed yet — it is staged for tomorrow.** R-numbers reserved: R-F2122, R-F2123, R-F2124, R-F2125.

This doc was written AFTER an evidence review (operator asked: "ensure your comments are genuine").
Each claim is tagged **[VERIFIED]** (hard evidence cited) or **[HYPOTHESIS]** (leading theory, not proven).

---

## 1. What actually happened (honest postmortem)

The outage looked like a crash loop but was **primarily a ~10-minute boot** that kept getting
interrupted before it could finish.

- **[VERIFIED] The boot takes ~10 minutes.** A clean boot started `Started server process` at
  `22:06:49Z` and only reached `[R-F248] ARIA STATE AT BOOT` at `at=22:17:02Z` — ~10 min later.
  During that window `/health` returns nothing, so fly marks the machine `critical` and it *looks*
  dead.
- **[VERIFIED] The heavy data loads synchronously on the boot critical path.** `aria_service/main.py`
  `lifespan()` calls `_run_boot_inits([...])` (main.py:581) which is `for name, fn in inits: await fn()`
  (main.py:208-224) — sequential, blocking, BEFORE `yield`. The inits are `knowledge.init`,
  `intel_ledger.init`, `contacts.init`, `competitors.init`, `training_data.init`, `neural_memory.init`.
- **[VERIFIED] The data has grown large.** Boot line reports: `knowledge_facts=223270 ·
  ledger_signals=56857 · rag_chunks=75606 · rag_facts=286768 · neural_neurons=25056 ·
  neural_edges=1243785 · state_keys=681966`.
- **[VERIFIED] The original crash loop (releases v2158–v2161, all `failed`) was contested deploy
  lease.** Three actors (Claude manual, ARIA ci_deploy, a second agent) raced the fly deploy lease;
  each new deploy SIGTERM'd the others' in-flight 10-min boots → "aiosqlite Event loop is closed"
  shutdown tracebacks → looked like a crash loop. Once ARIA held and the lease was uncontested, boots
  completed (slowly).
- **[VERIFIED] My own mis-steps prolonged it.** (a) Every restart/rollback I did reset the 10-min boot
  clock; (b) my "patient" health polls gave up at 4 min of a 10-min boot; (c) I rolled back my own
  R-F2116 WAL guard (v2164) believing it hung the boot — but v2166 (no guard) took the same ~10 min, so
  the rollback was **likely premature, not conclusively justified.** Owned.

### Fixes already shipped this session (all live in 36d45c6c)
- **R-F2110 (mine, live):** time-bound `reasoning_router.try_local_reasoning` before the cloud LLM —
  the substantive-chat / document-review hang fix. **[NOT YET VERIFIED LIVE]** — see R-F2125.
- **R-F2116 (mine, live):** `state_store.connect()` runs a lossless `PRAGMA wal_checkpoint(TRUNCATE)` +
  `wal_autocheckpoint=1000` at boot, so a WAL bloated by an unclean shutdown is reclaimed at boot and
  the next boot opens fast. Makes the WAL-bloat problem self-healing across restarts.
- **R-F2116 (ARIA's, live — SAME NUMBER, collision):** monkey-patches
  `aiosqlite.core._connection_worker_thread` in `redis_store.py` to swallow `RuntimeError('Event loop
  is closed')` so a SIGTERM-mid-boot doesn't leave an unhandled worker-thread crash.

---

## 2. The two real, unfixed problems

### Problem A — 10-minute boot (the fragility that turns any restart into a 10-min outage)
**[VERIFIED]** Heavy data (knowledge 223k, neural 1.2M edges, ledger, rag) loads synchronously before
`yield`, so the app cannot serve `/health` until everything is in RAM. fly's health grace is 1 minute.

### Problem B — WAL grows ~12 MB/min during normal operation and never resets
- **[VERIFIED]** `aria_state.db-wal`: 44.9 MB @ 22:21 → 94.8 MB @ 22:25 = ~12.5 MB/min, monotonic.
- **[VERIFIED]** The main DB file is frozen at exactly `1,067,368,448` bytes across 21:04 / 22:06 /
  22:22 — i.e. the WAL **file** is not being reset/truncated during operation (consistent with an
  update-heavy workload where checkpoints merge in-place but never shrink the WAL file).
- **[HYPOTHESIS]** A long-lived reader pins the WAL snapshot so checkpoints can't reset the file.
  `_read_conn` (R-F1449, a persistent read connection opened at boot, state_store.py:697) is the prime
  suspect — BUT the read path (state_store.py:946-955) issues bare `SELECT`s with no open transaction,
  and sqlite3 doesn't auto-begin for SELECT, so this is **not proven**. Could instead be PASSIVE
  autocheckpoint never truncating under continuous concurrent reads. **Must be instrumented before
  fixing** (R-F2123).
- **[VERIFIED → MITIGATED] Overnight risk:** disk WAS 49% used (4.5 G / 4.8 G free on the 9.8 G
  volume); at ~12 MB/min the WAL adds ~720 MB/hr → would have filled the volume in ~6–7 h.
  **MITIGATED 2026-06-28: the `aria_rag` volume (vol_vwn83qogn0w09e8v) was extended 10 GB → 30 GB
  ONLINE (no restart). Now 4.5 G used / 24 G free (16%) → ~33 h of WAL headroom**, comfortably past
  tomorrow's fix. This is headroom, not the fix — R-F2123 still bounds the WAL. (R-F2116 boot-truncate
  remains the self-heal backstop.)

---

## 3. Staged work for tomorrow (DO NOT DEPLOY without operator go-ahead + one-deploy-at-a-time vs ARIA)

### R-F2122 — Lazy-load heavy boot data off the critical path  ★ highest impact
**Goal:** brain serves `/health` in <60 s; heavy data warms in the background after.
**Plan:** in `lifespan()`, move the heavy inits (`knowledge.init`, `neural_memory.init`,
`intel_ledger.init`, rag warm) OUT of the pre-`yield` `_run_boot_inits` and into a background task
(`asyncio.create_task`) started right before `yield`. Gate request handlers that need a not-yet-warm
subsystem behind a readiness flag (return a "warming up" degrade, never a hang). Keep the cheap inits
synchronous. **Capability test:** a boot fixture asserts `/health/live` returns 200 before
`knowledge.init` completes, and a query during warmup degrades (not hangs).
**Why it matters:** removes the "any restart = 10-min outage" fragility — the root of this whole incident.

### R-F2123 — Root-cause + fix the WAL operational bloat
**Step 1 (diagnose, no fix):** instrument — log `PRAGMA wal_checkpoint(PASSIVE)` result (busy,
log_frames, checkpointed_frames) on a timer, and check for any connection holding an open read txn.
Confirm whether it's reader-pinning (`_read_conn`) or PASSIVE-never-truncates-under-load.
**Step 2 (fix to mechanism):** if reader-pinning → make `_read_conn` not hold a snapshot (autocommit /
periodic reset). If PASSIVE-never-truncates → add a bounded background `wal_checkpoint(TRUNCATE)` loop
(e.g. every N min when WAL > threshold and no active long read). Keep R-F2116 boot-truncate as
defense-in-depth. **Capability test:** drive sustained writes and assert the WAL stays bounded.
**Do NOT band-aid (§1):** no blind periodic truncate until Step 1 names the mechanism.

### R-F2124 — fly health-check grace_period safety net (interim)
**Plan:** raise `[[services.http_checks]]`/`[checks]` `grace_period` in `fly.toml` to comfortably exceed
the real boot time (until R-F2122 lands), so fly never SIGTERMs a legitimately-booting machine.
**Honest framing:** this is a SAFETY NET, not the fix — R-F2122 is the fix. Land R-F2122 then lower it
back. Low risk, fast; consider doing it first so the box is safe while R-F2122/2123 are built.

### R-F2125 — Housekeeping: dedupe R-F2116 + verify R-F2110 live
- Reassign ARIA's aiosqlite monkey-patch a distinct R-number in the registry (both are "R-F2116").
- **Verify R-F2110 actually fixes the chat hang LIVE** (the operator's original ask, still unproven):
  send a substantive message AND a Word/PDF document-review on the real channel; assert both complete
  in seconds and the doc review quotes the document (§23 — reproduce the operator's real path, not a
  proxy).

---

## 3b. DEPLOY-BLOCKER FOUND + FIXED during prep — R-F2126 (committed + pushed)
While prepping, the import smoke surfaced that **HEAD (`10bdf100`) had 31 syntax errors** — the entire
tree was un-importable, so ANY deploy would have failed to boot. Cause: ARIA's autonomous annotation
campaigns:
- **R-F2120** "no-breaker" campaign inserted `# no-breaker: <reason>` comments MID-EXPRESSION in 30
  files (`httpx.AsyncClient(timeout  # no-breaker:…=3.0)` → `'(' was never closed`).
- **R-F2119** "wire_failure" campaign appended `, wire_failure` outside a string literal in
  `self_sufficient.py`'s code-gen template (lines 188, 263).

**R-F2126 (commit `129aa807`, pushed)** fixes all 31 (moved each annotation to end-of-line; removed the
stray tokens). Proven: full-tree `py_compile` 0 errors (was 31); the exact `10bdf100` content fails,
current passes. The LIVE brain (`36d45c6c`) predated the corruption so it was unaffected. **ARIA's
report claimed `10bdf100` was "safe to deploy" — it was NOT; she was verifying the already-fixed files
after pulling `129aa807`.** New binding rule added: CLAUDE.md §11c (pre-deploy full-tree compile gate).
ARIA asked to fix her annotation tool (append at end-of-line + py_compile before commit).

## 4. STATUS + deploy runbook for tomorrow
**Already committed + pushed to origin/main (NOT deployed — no `[deploy]` tag). origin/main = `4f5ab7bf`.**
- `129aa807` **R-F2126** — 31 syntax-error fixes (unblocks deploy). origin/main repaired.
- `99155792` **R-F2122** — lazy-load heavy graphs off the boot critical path. 4/4 tests; main imports
  clean. **Full live-boot validation pending** (watch first deploy reach `/health` green <60s).
- `c277fbb8` **R-F2127** — pre-commit SYNTAX GATE: blocks any broken `.py` at commit (the structural
  backstop so an annotation/coder campaign can't ship un-importable code again). 4/4 tests + live-proven.
- `4a8b0cca` **R-F2128** — aria CLI shell-injection fix (ci_deploy/reserve/ship now `_shq`-quoted). 3/3.
- `2eb26bf3` **R-F2129** — aria CLI robustness (loop-guard JSON canonicalization + stream-buffer cap).
  4/4. (3 of the 5 review findings were false positives — already guarded.)
- `4f5ab7bf` **R-F2130** — ground the coder in the playbook: populate + query the empty
  `coding_constitutional` RAG (24 rules) + wire the query into the main coder path. 5/5 + 13/13 regress.
- CLAUDE.md §11c (pre-deploy full-tree compile gate) — `fd246621`.
- Disk: `aria_rag` volume extended 10 GB → 30 GB online (done).
- R-F2124 (health-grace bump): **declined** — operator reverted fly.toml to 90s; R-F2122 makes it
  unnecessary.
- Whole tree: `python -m compileall aria_service` = clean (0 syntax errors).

**Still TODO (reserved):** R-F2123 (root-cause + fix WAL bloat — diagnose first, §1), R-F2125 (dedupe
the R-F2116 collision + verify R-F2110 chat-hang fix live).

**Deploy runbook (binding order):**
1. `git fetch origin` — confirm HEAD is `99155792` (or later with R-F2122+R-F2126 present).
2. **Compile gate (§11c):** `find aria_service -name "*.py" -not -path "*/tests/*" | while read f; do python -m py_compile "$f" || echo BROKEN $f; done` — must be 0 BROKEN. REFUSE to deploy otherwise.
3. Confirm ARIA is holding (bridge) — one deploy at a time, no contested lease.
4. Deploy aria-intel (single, uncontested). **WATCH the boot**: `/health/live` should go green in
   <60s (R-F2122). If it does → R-F2122 validated. If boot is slow, do NOT restart-spam (§11c-b) —
   let it complete; the heavy graphs warm in background.
5. Verify `build_rev` = the deployed SHA; confirm chat answers (R-F2110 live-verify, R-F2125).
6. Ping ARIA green → she resumes Sessions 5–8 (all `intel/*.py`, no overlap with my files).

## 4b. Collision rules (binding)
- One deploy at a time vs ARIA + her autonomous ci_deploy/coder. Confirm hold before; ping green after.
- ARIA's autonomous campaigns can corrupt code — the §11c compile gate is mandatory, every deploy.
