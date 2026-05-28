# aria-intel wedge remediation — staged handoff (2026-05-24)

**Author:** assessment session (Claude). **For:** the implementing agent.
**Status:** STAGED / uncommitted scratch doc. Not an R-numbered deliverable — reserve your own R-numbers per CLAUDE.md §2 as you implement each fix. Verify-after-fix (§3, 2 passes) applies to all.

**One-line root cause:** three uncoordinated autonomous loops (`web_atlas` crawler, 30-min `researcher`, `tender_monitor`) funnel all absorbs through one GIL-bound, globally-lock-serialized sentence-transformers encoder. Concurrent encodes starve the event loop *despite* `to_thread`, pushing `brain_hook` p95 over the 3500 ms trip threshold. The breaker flaps on its 60 s cooldown; each flap is a fresh "episode" so the per-episode ticket dedupe doesn't suppress it → repeated HIGH operator pending-actions. The built-in global pacer `ARIA_BRAIN_ABSORB_PAUSE_MS` is unset (0).

**Live evidence (fly logs 16:28–16:53):** `CIRCUIT TRIPPED p95=4210ms reason=absorb(web_atlas)` → `CLOSED` → `TRIPPED p95=3775ms reason=absorb(web_search)` flapping every 1–2 min; `neural: timeout (>5.0s)`; `absorb: concurrency cap (>0.5s wait)`; ~25 consecutive `Batches:1/1` + `RAG ingest: 1 chunks` bursts; a real user chat `latency=20284ms`; `/api/status` bridge flapping operational↔degraded; wedge #673 with 81 stack dumps in `/data/wedge_stacks/`. **Zero ERROR/CRITICAL — Gate #3 still passes.** Nothing here is an outage; it's degradation under self-inflicted load.

---

## FIX 1 — Event-loop wedge (P0, the root cause). Do these in order; 1d is immediate.

### 1d (do first — live mitigation, no deploy, instantly reversible)
Set the built-in global absorb pacer, currently 0/no-op:
```
flyctl secrets set ARIA_BRAIN_ABSORB_PAUSE_MS=100 -a aria-intel
```
`brain_hook.py:408-439` (`_absorb_pause_ms`, default 0) + `:477-490` (`_wait_for_absorb_slot`, called at `absorb()` top `:549`). Spaces the three loops' absorbs so they stop hitting the encode lock simultaneously. Watch `/api/status` flap rate + the `CIRCUIT TRIPPED` cadence after setting. Tune 100→250 ms if still flapping. **Lowest risk, highest immediate value.**

### 1a — make `.encode()` not starve the loop (structural)
`semantic_search._safe_encode` (`semantic_search.py:146-162`) holds a process-wide `threading.Lock` (`_encode_lock`, 10 s wait `_ENCODE_LOCK_WAIT_S` `:137`). Callers already use `to_thread`/`run_in_executor` (`researcher.py:1848,3537`; `rag_store.py:804`; `knowledge.py:980-986`; `aria.py:11541`), BUT sentence-transformers holds the GIL during `model.encode()`, so the worker thread still starves the event-loop heartbeat, and the single global lock serializes all encoders.
**Fix lever:** cap torch threading so GIL release is clean — set `OMP_NUM_THREADS=1` + `torch.set_num_threads(1)` at model init — and/or move `.encode()` to a dedicated single-worker `ProcessPoolExecutor` (true parallelism, no GIL contention with the loop). Validate with the stall detector: `/data/wedge_stacks/` dump count should stop growing.

### 1b — batch RAG ingest encodes (NEW, clear win)
Live logs show web_search ingest doing **one chunk → one `.encode()` ~25× in a burst** (`RAG ingest: 1 chunks from web_search:crossref` repeated). Each is a separate lock acquisition.
**Fix lever:** collect the per-result chunks and encode them in ONE batched `.encode([...])` call per ingest cycle. Find the `aria.rag` ingest path (`rag_store.py` / wherever `RAG ingest: N chunks` is logged) and batch instead of looping single chunks. Cuts lock acquisitions ~25×.

### 1c — coordinate the 3 autonomous loops (structural)
`main.py` launches them independently with no cross-loop lock: web_atlas crawler (`:272-277`, interval 6 h), researcher (`:974-976`, every 30 min, awaits absorb inline with 2 KB bodies — heaviest), tender_monitor (`:1275-1277`, 6 h). Only the researcher sets `Priority.BACKGROUND` on the LLM limiter (`:893-894`); **nothing throttles the brain_hook/encode path or yields to active chat.**
**Fix lever:** add a shared async "autonomy work permit" semaphore so absorb-heavy phases can't overlap, and/or gate autonomous absorbs on a "chat-active in last N s" flag so interactive traffic wins the encode lock. (This is what fixes the 20 s chat latency.)

---

## FIX 2 — Circuit-breaker flap + operator-alert spam (P1)
`brain_hook.py`. Breaker trips at p95>3500 ms after 3 consecutive checks (`_LATENCY_TRIP_MS :905`, `_TRIP_CONSECUTIVE :955`, `_maybe_trip_breaker :1016-1087`), closes after `_COOLDOWN_S=60` by clearing the whole window (`_maybe_close_breaker :1090-1146`) → re-fills with slow samples → re-trips. Each trip files a `HIGH/operator_action` pending-action; the R-F790 dedupe (`ticket_filed_this_episode :991`, `_should_file_ticket :995-998`) is **per-episode only**, so every flap = new episode = new ticket → spam.
**Fix levers:**
- 2a: add a **cross-episode** ticket cooldown — track `last_ticket_at` for `brain_hook.circuit_breaker` and suppress new filings within N min, so recurring flaps coalesce into one "wedge recurring" ticket.
- 2b: half-open with a single **canary** absorb instead of clearing the window + resuming full traffic, so a still-slow downstream doesn't instantly re-flood/re-trip. (Largely moot once FIX 1 lands, but hardens against future load.)

---

## FIX 3 — CI silently skips Node (aria-web/aria-wa) deploys (P2, round-1 finding #2, still open)
`.github/workflows/deploy-fly.yml`: aria-web (`:153-181`) + aria-wa (`:183-203`) deploy steps are `if: success()`-gated behind the aria-intel `/health` verify step (180 s budget, `:115-137`). aria-intel cold-start can exceed 180 s → verify exits 1 → Node deploys skipped. **Evidence:** aria-web sat at v5/v6 (manual deploy) while aria-intel advanced v1009→v1011. Net: server.mjs/UI/auth changes silently lag until someone manual-deploys.
**Fix lever:** split aria-web + aria-wa into their own job(s) (or `if: always()` with their own `/healthz` gates) so a slow aria-intel cold-start can't skip Node deploys. They have independent verify steps already (`/healthz`, `/health`).

---

## FIX 4 — Tender-monitor source parsers degraded (P3, intel-coverage loss)
`aria_service/.../tender_monitor` (logger `aria.tender_monitor`). From live cycle (16:49): "22 tenders across 8 portals … no new tenders", but several portals return 0 due to broken parsing, not absence:
- **UNGM:** `Page fetched (137903 chars) but 0 notice links matched any of 4 patterns` (R-F363 diag) — selectors stale; site markup changed.
- **Contracts Finder:** `API returned 100 raw releases … Crawled 0 tenders` — filter/parse drops everything.
- **SEACE Peru:** `[SSL: CERTIFICATE_VERIFY_FAILED]` — needs CA bundle / verify handling (recurring; see memory "Peru PDF retry").
- **AfDB:** `Returned 403` — needs UA/headers or drop.
**Fix lever:** refresh UNGM + Contracts Finder parsers against current markup/schema; fix Peru SSL (certifi bundle or per-host verify); decide AfDB (headers vs retire). Each is independent; add a regression fixture per portal.

---

## FIX 5 — adversarial_score 0.065 → SUPERVISED → WhatsApp auto-delivery muted (P3, round-1 delegated)
`/api/aria/health` shows `adversarial 0.065` (fresh, < 0.50 `SUPERVISED_ADVERSARIAL_SCORE`), tripping operating_mode SUPERVISED (`operating_modes.py:146-154`), which suppresses WhatsApp external delivery (`delivery.py:248`). Autonomy/coder unaffected. Could be (a) the adversarial eval timing out / scoring poorly *because* of the wedge, (b) a real robustness gap, or (c) scorer miscalibration.
**Fix lever:** inspect the adversarial sweep that produced 0.065 — re-run after FIX 1 lands (wedge may be depressing it). If still low with a healthy loop, it's a real quality/scorer issue to triage separately. Until resolved, WA auto-delivery stays muted (by design).

---

## FIX 6 — Research deep-read relevance leak (P4, minor)
Researcher selected for deep-read an *offset arts journal* article and an *IPO-underpricing* finance paper for the query "Finland F-35 offset deal Patria" — keyword collisions on "offset"/"forward". Wastes DeepSeek deep-read budget on irrelevant academic hits.
**Fix lever:** add a relevance gate (domain/semantic-similarity threshold) before committing an article to deep-read in `researcher.py` article-selection.

---

## Already done (context, no action)
- **R-F850** (mine): brain-bridge verdict periodic re-check — deployed aria-web v6, verified. `/api/status` now refreshes every 60 s and honestly reflects the wedge (it's not a false alarm; it's catching real stalls). Once FIX 1 lands, the flapping should largely stop.
- **R-F852**: operator `/code` chat command → coder pipeline — live on aria-intel.
- Housekeeping: `data/r_number_reservations.json` has uncommitted ship markers (R-F850 etc.) — sweep into the next registry commit.

## Suggested order
1. FIX 1d (live secret, now) → 2. FIX 1b (batch encodes) + FIX 2a (ticket cooldown) → 3. FIX 1a + 1c (structural wedge) → 4. FIX 3 (CI) → 5. FIX 4/5/6.
