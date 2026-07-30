# Live-log deep DD — 15 cycles, aria-intel, 2026-07-30

**Window:** 07:48Z → 08:55Z (~50 min continuous tail + buffer pulls), 15 sampling cycles.
**Builds observed:** `a0ee0b99` → `28c6bd7a` (two restarts mid-review, both new releases v2737→v2738+).
**Method:** `flyctl logs -a aria-intel` (5-min foreground tails), plus live probes of `/health`, `/health/live`,
`/phase/gates`, `/api/aria/mastery/heatmap`, `/api/aria/brain/stats`, `/api/aria/health/perf`.

Every claim below cites a log line, a `file:line`, or a probe. Anything not proven is labelled **UNVERIFIED**.

---

## P0 — user-visible, revenue-affecting

### 1. A real customer document was refused as malware (probable false positive)
```
R-F1853 read-document blocked unsafe upload (Offer Sikorsky UH-60A 29th of July 2026_draft.pdf):
  PDF embedded JS (HIGH); INT3 breakpoint chain (HIGH); Base6…
R-F873 async read-document job b3394fe9ac10 failed: 422: document blocked by content scan
```
Root cause: `intel/content_scanner.py:80-107` matches **raw byte substrings** over the whole file.
Two of the patterns cannot distinguish malware from ordinary binary content:

| pattern | line | problem |
|---|---|---|
| `b"DDE"` ("XLSX DDE formula") | 92 | a 3-byte substring — matches inside any compressed stream |
| `b"\xcc\xcc\xcc\xcc"` ("INT3 breakpoint chain") | 98 | 4 repeated bytes — routine in image/font data |

**MEASURED** (12 benign local files, PDFs + PNGs): `DDE` fired on **4/12**, `INT3` on **1/12**.
The Sikorsky block cites `INT3 breakpoint chain` as a HIGH threat — so the block is very likely spurious.

*Not proven:* the base64 trigram patterns (`TVp`, `c2g`, line 103-107, 3 chars + `re.I`) look structurally
unsafe but did **not** fire on my sample. Do not claim they are broken without a repro.

**Fix direction:** anchor the binary signatures (PDF `/JavaScript` only inside a `/Names` or `/AA` dict, not raw
bytes); require `DDE` in an xlsx formula context; drop or heavily raise the threshold on NOP/INT3 for
non-executable MIME types. Then re-scan the operator's Sikorsky PDF as the capability test (§3c).

### 2. The LLM chain fails outright while `/health` reports `operational` / `resilient: true`
Cycle 15, one 5-min window:
```
14 × Article analysis failed: [fallback] all LLM providers failed — try again in a minute
     Code generation LLM call failed: [fallback] all LLM providers failed
     [Self-Improve] Diagnosis failed for aria_service/autonomous/engine.py: all LLM providers failed
     [Self-Improve] Diagnosis failed for aria_service/intel/web_integrity_agent.py: all LLM providers failed
```
Concurrent probe of `/health`:
```
"status":"operational"  "resilient":true  "active_providers":["anthropic"]
cooling: deepseek (billing, 85368s)  deepseek_backup (billing, 55376s)
```
`deepseek` + `deepseek_backup` are both on **24h HARD billing cooldown**. `gemini`/`groq`/`openai` are
skipped — no API keys. So the chain is depth-1 on `anthropic`, and it is *also* failing in practice.

This is the §14 fallback-transparency rule applied past its limit: "cooling ≠ broken" is right, but the
status is derived from **configuration** (a provider is listed active) rather than from **outcomes**
(recent calls all failed). `resilient: true` during a total-failure window is not honest reporting (§19d).

**Fix direction:** make `resilient` a function of recent call outcomes, not chain membership — e.g. flip to
`degraded` when the last N chain attempts all returned `all LLM providers failed`. The signal already exists.

### 3. `llm_fallback_stats` cache has never served a single hit
`{"size":0,"max_size":100,"hits":0,"misses":491,"hit_rate":0.0}` — and after a restart, `misses:79, size:0`.
A cache with **size 0 after 491 misses is never being written**. Against §15 (pay-once-remember-forever)
this is a standing cost leak. **UNVERIFIED**: which write path is missing — needs a read of the provider's
`get_stats()`/put path.

---

## P1 — measurement honesty (the Phase A gates)

### 4. `web_integrity_agent` reports "9 passed, 0 failed" while 2 of 9 endpoints 4xx every cycle
**MEASURED**, every cycle for the whole window:
```
"POST /api/aria/report HTTP/1.1" 400
"GET /api/aria/autonomous/status HTTP/1.1" 403
… [web_integrity] cycle complete: 9 endpoints, 9 passed, 0 failed (0 critical), 0 patterns actionable
```
Two independent defects:

**(a) `intel/web_integrity_agent.py:256-259`** — a 4xx appends a *warning* and never sets `passed = False`;
only 5xx fails. And `expected` fields are checked **only on 2xx** (line 272), so a permanently-4xx endpoint
has its content contract silently unverified. This is the "guard that cannot fire" class.

**(b) The two probes are structurally incapable of passing:**
- `/api/aria/report` — the agent POSTs `json={}` (line 238); `routes/aria.py:15597` requires
  `report_type` + `subject` → **guaranteed 400 forever**. Its declared `expected: {"sections","sources"}`
  has never once been evaluated.
- `/api/aria/autonomous/status` — R-F2139 operator-tier scoping (`routes/aria.py:297-298`,
  `_OPERATOR_ONLY_RE` matches `/api/aria/autonomous/`) rejects the internal token with 403. The R-F2561
  comment at line 205 still says the token is attached "so probes don't 401" — it no longer 401s, it 403s.
  Exactly the failure R-F2567 fixed for `coder/llm` ("self-coding loop fixed=0"), recurring on another path.

**Fix direction:** 4xx ⇒ `passed = False` unless the endpoint declares an `expected_status`; give `/report`
a real minimal body; either exempt `autonomous/status` (a read-only view) from the operator regex or give
the probe the operator token. Guard: a test asserting no probe can report `passed` on a non-2xx.

### 5. Gate #2 heatmap floor has collapsed 0.507 → **0.003** — partly honest, partly instrument
Live: `gate_2_heatmap_floor: {"value":0.003, "pass":false}`, floor cell `technical × central_africa`.

The collapse is **not** uniform — `/api/aria/mastery/heatmap` shows healthy cells alongside dead ones
(`market_intel×southern_africa 0.965`, `finance×gulf 0.974` vs `technical×central_africa 0.003`,
`compliance×latam_lusophone 0.004`). So the grader *does* pass sometimes; this is not a blanket failure.

But the grader punishes "could not measure" as "measured and wrong".
`autonomous/tasks.py:2266-2297` `_grade_researched_cell` returns **`False`** on all of:
- `except Exception` around `try_local_reasoning` (2287)
- `not local.get("answered")` (2289)
- `except Exception` around `_quick_similarity` (2296)

and the caller (2406-2411) then calls `update_regional_mastery(correct=False)` unconditionally — **there is
no skip path**. Two consequences:

- `reasoning_router.try_local_reasoning` documents `answered: False` as a **routing signal meaning
  "escalate to cloud"** (`reasoning_router.py:220-224`), not "ARIA is wrong". It also returns `answered:False`
  for deliberate bypasses (Stage 0 self-infra, Stage 0.5 self-capability). All of these are graded as wrong.
- CLAUDE.md §1 states of R-F2660 that "a grader error SKIPS the update (never fabricates a pass)". For this
  shared grader that is **not what the code does**. The doc and the code disagree.

The EMA is `score += alpha*(obs-score)`, `alpha = 0.1*weight` (`student.py:2372-2382`). Reaching 0.003 from
`INITIAL_MASTERY 0.5` at `weight=0.5` needs ~100 consecutive `correct=False`. Combined with finding #2 —
14 `article analysis failed` in five minutes — a **provider-billing outage is being recorded as ARIA not
knowing things.**

**UNVERIFIED and worth measuring before acting:** what share of the floor is real starvation vs. grader
artifact. The clean test is to add the missing tri-state (skip on error/unanswered, as the gate layer already
does per R-F2639) and watch whether the floor recovers. Do **not** close the gate by relaxing it (CLAUDE.md §1).

### 6. `neural_ok` is a constant — the neural tier reports False 100% of the time
**MEASURED:** 206/206 absorbs in one 5-min window logged `absorbed [mastery=True knowledge=True neural=False]`,
with **zero** `brain_hook … errors` WARNINGs.

`brain_hook.py:777` sets `neural_ok: False` and **never assigns True anywhere in that file**; the real
assignment is `brain_hook_bg.py:265`. Reading the branches: every failure path (concurrency cap 229,
interactive-defer 252, tier error 267) *appends an error* — and an error makes the logger take the
WARNING branch (`brain_hook_bg.py:176-182`) instead of printing "absorbed". Since we see "absorbed" with
`neural=False` and no warnings, the only remaining branch is the entry guard
`if … text_for_neural and len(text_for_neural) > 50` (line 214) failing.

The dominant producer is `cross_tier:crucix_briefing_signal` (58 of 66 hooks in cycle 8; ~700/hr), emitted by
`aria-web` at `apis/briefing.mjs:784`. Both sinks pass `detail=content[:2000]`
(`brain_signal_consumer.py:47`, `routes/aria.py:16787`), so the implication is that `content` itself is
≤50 chars. Consequence: those signals also skip the RAG detail-fact write (`brain_hook_bg.py:122`, `>200`
guard) — they write a sub-50-char knowledge fact and nothing else.

**UNVERIFIED:** the actual `content` string. Confirm by reading one stored fact under topic
`cross_tier:crucix_briefing_signal` before changing the producer.

### 7. Neural memory lost 97.6% of its edges across a restart
```
[R-F251] STATE REGRESSION DETECTED — counters dropped >5% since previous boot:
  neural_edges: 3432 → 82 (-97.6%)
```
CLAUDE.md §11c records ~1.2M neural edges historically; the pre-restart figure was already 3,432. Combined
with #6 (never absorbing) the neural layer is failing in both directions — not growing, and not surviving
restarts. This is a §7 (infinite memory, no eviction) violation. ARIA's own detector caught it, which is the
system working; nothing appears to act on it.

---

## P2 — capacity and waste

### 8. The box is I/O-saturated; the event loop freezes for up to 10s
- Profiler, every snapshot: **~38.7% `aiosqlite/core.py:_connection_worker_thread`** + ~33-35%
  `concurrent/futures/thread.py:_worker` ≈ **73% of samples in DB/thread work.**
- `[continuous_profiler] Main loop heartbeat stale … (stall #58 … #66)` — reached **#66 in ~3h uptime**;
  after the restart, #8 within ~10 min. Roughly **1 stall/min sustained.**
- `[R-F703] event loop heartbeat did not tick for 10.37s / 7.94s / 6.37s / 6.02s / 5.98s / 5.97s`
- `state_store.get(crucix:aria:cost:month:2026-07:reserve) timed out after 5s — DB may be bloated`
- `[R-F2185] load-shed: backing off background work — pressure=1.00`
- Snapshot cost: `knowledge: 392,530 facts in 202 shards, 57,136,024 bytes gzip` and
  `intel_ledger: 72,380 signals, 6,440,828 bytes gzip`, written periodically.

Fact count has grown ~223k → **392,536** (+76%) since CLAUDE.md §11c was written, and #6 shows a large share
of new facts are sub-50-char telemetry. **Root cause candidate (UNVERIFIED):** the periodic full-blob
snapshot writes are what stall the loop — the profiler's non-idle frames include
`knowledge.py:_write_to_disk_atomic:670` and `_write_facts_sidecar:490`.

### 9. Free-source stack is rate-limit saturated; breakers cycle continuously
Tripped during the window: `opensanctions.org` (CLOSED→OPEN, `reason=rate_limit`, then HALF_OPEN→OPEN,
backoff 2400s), `semantic_scholar` (600s→1200s), `openalex` (9600s), `archive_is` (3600s), `wayback` (3600s),
`search:duckduckgo` (600s). Plus `[SAM.gov] API returned 429 … exceeded your quota` and
`DuckDuckGo returned 202 (rate-limited/queued)`.

**Contradiction worth chasing first:** the log says
`OpenSanctions rate-limited (free tier: 1 req/sec). Set OPENSANCTIONS_API_KEY for unlimited access.`
but `flyctl secrets list -a aria-intel` shows **`OPENSANCTIONS_API_KEY │ Deployed`**. Either the client
isn't reading the key, the key is rejected, or the message is unconditional. A deployed key that buys
nothing is a silent regression on a sanctions source — check this before anything else in §9.

### 10. A scraper is silently returning zero, not failing
```
[UNGM] Page fetched (140923 chars) but 0 notice links matched any of 4 patterns.
  R-F363 diag: 0 /Public/Notice/* hrefs found (any shape), 0 embedded n…
```
The fetch succeeds, so nothing errors — the source just yields nothing forever. The R-F363 diagnostic is
doing its job; nobody is acting on it. Also `[SEACE Peru] Crawl failed: [SSL: CERTIFICATE_VERIFY_FAILED]`.

### 11. Two always-empty scheduled tasks burn ~720 runs/day
`DRAIN-COLLAB-BRIDGE (cron='*/2 * * * *')` fired every 2 minutes for the entire window and returned
**`0 actions, normal`** every single time — plus a `self_restart` checkpoint write per run.
`HOURLY-NEWS-MONITOR` and `RESEARCH-WEAK-CELL-HOURLY` likewise logged `0 actions, normal`.
Also every ingest sweep: `Ledger ingested 0 new signals (10 propaganda-tier signals skipped at boundary)` —
the **same 10** signals re-examined and re-skipped forever.

### 12. Gaps accumulate with the coder lane off; two components disagree on the count
```
[gap_detector] scan complete: 61 → 58 → 57 → 68 → 72 → 77 actionable gaps
[aria_coder] 3 actionable gaps -- fixing top 20
[aria_coder] fix_gap REFUSED for … — ARIA_CODER_ENABLED='0' (the autonomous coder lane is off)
```
The lane being off is the operator's 2026-07-23 decision (1 gold fix in 52 attempts) and is **correct** —
but **CLAUDE.md §21c still states the loop "must stay ENABLED (`ARIA_CODER_ENABLED=1`)"**. The binding doc
contradicts the live, deliberate configuration; a future session reading §21c as the floor would switch it
back on. §21c needs amending to record the pause and its rationale.

Separately, `gap_detector` says 77 actionable and `self_coder` says 3 in the same window — a 25× divergence
in what "actionable" means between producer and consumer. **UNVERIFIED** which is right; `self_coder.py:518`
applies further filters (`pending_skip`, `protected_file_gaps`). Worth reconciling, because 77 is what the
dashboard would show while 3 is what would ever be worked.

### 13. Self-improve is blocked on the same LLM outage
```
[Self-Improve] skipping aria_service/intel/circuit_breaker.py — 3 consecutive parse failures,
  TTL 86400s remaining before retry
```
Plus the two `Diagnosis failed … all LLM providers failed` lines from #2. Self-improvement is inert while
the chain is down, and the 24h TTL means a transient outage costs a full day per file.

---

## What is genuinely healthy

- **Gate #4** (quarantine 4/4 closed), **#5** (3/3 env vars), **#6** (500-Q eval frozen, hash intact
  `a07b6af760ad7f44`) all pass honestly.
- **Gate #1 composite is 0.801 — it beats the 0.71 target.** It fails only on `confidence 0.30 < 0.60`,
  i.e. sample volume, exactly as `memory/gate1_blocked_on_sample_volume_2026_07_28.md` records.
- `diagnostic: {"overall":"GREEN","pass":76,"warn":0,"fail":0}`; loop p50 0.3ms / p95 1.1ms.
- The **honest-reporting machinery works**: R-F198 marked an adversarial run DEGRADED rather than scoring it
  (`23/23 attacks had ≥80% empty responses` — a provider blip, correctly excluded); R-F251 caught the neural
  regression; R-F2852 deferred a mastery load rather than clobbering it (the R-F2664 protection firing);
  R-F1920 noticed the live/origin drift; R-F363 diagnosed the UNGM zero-match. **ARIA's self-observation is
  in good shape. The gap is that almost nothing consumes these signals.**

---

## Recommended order

1. **Content-scanner false positives** (#1) — a customer document is being refused *now*.
2. **OpenSanctions key** (#9) — a deployed key that behaves as free tier, on a USP source.
3. **`resilient: true` during total LLM failure** (#2) — honesty defect on the main health surface.
4. **web_integrity 4xx-counts-as-pass** (#4) — cheap, and it is currently blind on 2 of 9 endpoints.
5. **Grader tri-state** (#5) — then re-measure the gate #2 floor before drawing any conclusion from 0.003.
6. **neural tier** (#6/#7) — confirm the briefing-signal payload first, then the entry guard.
7. Capacity work (#8) and the waste items (#10-#13).

## Housekeeping

- **A second agent is committing to this tree.** During the review, `origin/main` advanced
  `b43f7000` → `5aedf973` (R-F3451) → `28c6bd7a` (R-F3450+R-F3453) and deployed twice
  (v2737→v2738). Per `memory/two-agents-one-tree-hazard.md`, do not `git add -A` and verify commit
  **contents** before pushing anything from this session.
- No R-numbers reserved by this review — it is read-only. Each fix above needs its own reservation (§2).

---

# Follow-through — 6 R-numbers shipped from this review (2026-07-30)

All verified live on aria-intel. Two corrections to the register above are marked.

| R# | What | Live sha |
|---|---|---|
| R-F3457 | Content-scanner false positives (finding #1) | `005609ba` |
| R-F3464 | Stall detector blamed sleeping threads (corrects #8) | `6078a1c0` |
| R-F3467 | Lazy `playwright` import blocking the loop | `2869100c` |
| R-F3468 | Agent registry ran blocking sqlite on the loop | `9e1ec0cb` |
| R-F3469 | Mislabelled file treated as a threat, not a routing signal | `9e1ec0cb` |
| R-F3473 | SSRF guard did a blocking DNS resolve on the loop | `afbb28c8` |

## Correction to finding #8

**"~73% of samples in aiosqlite + threadpool" was wrong, and it was my error.** Those
frames are threads PARKED in blocking waits — `_connection_worker_thread` on a C-level
`SimpleQueue.get`, `_worker` on `work_queue.get`. They appear on every sample, so they
dominated the report on an idle box more than a busy one. It was a census of sleeping
threads, not I/O pressure. `main.py:1766` already recorded that this had cost two prior
review cycles; this review was nearly the third.

**The R-F3252 GIL-starvation theory is also not the current cause.** Live thread census
after R-F3464: `aiosqlite_workers: 9` against R-F3252's 56 (peak 140). R-F2754's leak
reaper is holding.

## What the stalls actually are

Once R-F3464 made the detector report the LOOP THREAD's stack, each stall named its own
cause, and they were four different things — none of them aiosqlite:

1. `playwright/_impl/_locator.py:<module>` — a lazy import; measured at **~7.5s** in the
   boot log once pre-warmed (R-F3467).
2. `agent_registry.py:_db_tick_heartbeat` — synchronous `sqlite3` commit/fsync inside an
   async method; an AST sweep found **12** such calls, all fixed as a class (R-F3468).
3. `socket.py:getaddrinfo` via `security.validate_url` — blocking DNS in the SSRF guard,
   slowest exactly when it fails (R-F3473).
4. `trafilatura/main_extractor.py:_extract` — CPU-bound HTML parsing on the loop.
   **OPEN**, tracked; a different class from the three above.

The generalisable lesson: **verify the instrument before acting on it.** Fixing the
detector was worth more than any single stall fix, because it turned a recurring
mystery into a queue of individually named, individually fixable causes.

## Measured outcomes

- Content scanner: benign files blocked **6/13 → 0**. Detection intact — EICAR, PDF
  `/JavaScript` + `/Launch`, DOCX VBA, XLSX `DDEAUTO`, base64 PE/ELF/shell, disguised
  PE/ELF/shebang, polyglot, and a real zip bomb renamed `.pdf`.
- One long-red baseline guard closed: `test_rf450_generic_zip_renamed_as_pdf_does_not_
  invoke_pdf_parser` (`docs/suite_baseline_2026_07_30.md:205`).
- New stall evidence is now durable on `/api/aria/health/perf` →
  `heartbeat.last_stall_loop_stack` / `last_stall_threads` / `threads_now`.

## Still open from the original register

Findings #2 (`resilient: true` during total LLM failure), #3 (fallback cache never
written), #4 (web_integrity counts 4xx as pass), #5 (gate #2 grader tri-state), #6/#7
(neural tier), #9-#13. Plus the two residuals tracked as tasks: trafilatura offload, and
DOCX extraction returning empty for a renamed upload.
