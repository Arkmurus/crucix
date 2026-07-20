# ARIA Foundation — HA Re-architecture Design (R-F2813)

**Status:** DESIGN — awaiting operator sign-off before any code.
**Author:** Claude session `eb5d6b39` · **Date:** 2026-07-20
**Directive:** operator — *"we not looking for cheap, we need the most robust and bulletproof option"* / north-star: autonomous reasoning with 99.99% zero fabrication, *"no corners cutting."*
**Decision locked (operator, 2026-07-20):** design-doc-first · **LiteFS full HA** state layer.

---

## 0. Purpose

Eliminate the failure **classes** — not the MTTR — behind the recurring "ARIA keeps breaking." This is §1's ROOT-CAUSE rule applied at the foundation: every fix below removes a failure class structurally. Scope is availability/resilience of the aria-intel brain and its surfaces (WA/web); it does **not** change brain reasoning, the DD pipeline, or the honesty/verifier moat.

---

## 1. Evidence base — the 360° DD (2026-07-20, 5 read-only probes)

All findings are code-cited; the full probe records are in the session transcript. Summary:

| Axis | Structural weakness | Evidence |
|---|---|---|
| Boot | `/health/live` greens before LLM/RAG ready; **no `/health/ready`** → Fly/WA/web route to a not-ready brain | `routes/aria.py:24414`, `main.py` (llm init bg `:1701/1782`, `knowledge_ready`/`neural_ready` `:979-980`) |
| WA self-heal | Watchdog keys only on `isConnected` boolean; a **zombie socket** stays `connected=true` forever → never restarts | `aria_wa_listener.mjs:3578`; no `_lastInboundAt` anywhere; `makeWASocket` sets no `keepAliveIntervalMs` `:2026` |
| Deploy | **1 machine + 1 single-attach volume + no rolling + no deploy lease** → every deploy = full outage; manual+ci_deploy race & SIGTERM each other's boot | `fly.toml:24-26,122-132`; `deploy.sh` (no lock, ships working tree); `coder_tools.py:293-388` |
| Proprioception | R-F2178 `/liveness/beat` machinery is **built but unfed** — no node surface POSTs a beat; heartbeat reports `!!sock` (object exists) not real work → brain believes WA healthy through the whole outage | `liveness.py:89-103` only producer; `aria_wa_listener.mjs:1096`; `main.py:3050-3057` |
| State store | Single **uninterruptible** aiosqlite writer thread; wedge class *mitigated* (`os._exit`→cold boot) not *closed*; `None`-on-error reads = false-clean risk | `state_store.py:109,144,392,864-871,1048-1065,2222-2230,2461-2476` |
| Telemetry | Designed backpressure (`absorb: concurrency cap → shed to WAL`) logged as WARNING **"N errors"** → looks broken while working | `brain_hook_bg.py:176-178`, `brain_hook.py:1501` |

### What is already solid — MUST be preserved, not "fixed"
- R-F2122 heavy-graph deferral (server `yield`s in seconds; graphs warm in background). `main.py:967-1064`.
- state_store wedge watchdog: 15s probe → 45s reconnect → 180s `os._exit` + forensic dump. Real, complete self-heal. `state_store.py:909,988,943,1048`.
- Absorb load-shedding + **`memory_wal` safety net** (a shed fact is never lost). `brain_hook_bg.py:150-160`.
- Runaway-loop protection: R-F2073 singleton election, R-F1610 bounded respawn, R-F2668 one-shot fix.
- `/health/live` is DB-independent (a wedge can't blackhole the LB). `main.py:4550-4576`.
- Web tier degrades gracefully (stateless, 45s fast-fail → 503). `server.mjs:2694-2795`.

**Design rule #1: no change may regress any item in this list.** Each stage's capability test asserts the preserved behaviour still holds.

---

## 2. Root cause in one sentence

> ARIA is **one process (one event loop, one uninterruptible SQLite writer thread) on one machine with one non-shareable volume**, and every surface depends on it, treats "alive" as "ready," and cannot self-heal when it isn't.

The bulletproof target removes each "one" from that sentence, in a safe order.

---

## 3. Target architecture

```
                 ┌───────────────────────── Fly app: aria-intel ─────────────────────────┐
   WA / web  ──▶ │  Fly LB  ──routes only to machines passing /health/ready──▶  ┌──────┐  │
   (.internal)   │                                                              │ M1   │  │  PRIMARY  (LiteFS writer)
                 │                                                              │ (RW) │◀─┼── writes
                 │                                                              └──┬───┘  │
                 │                                                    LiteFS repl │       │
                 │                                                              ┌─▼────┐  │
                 │                                                              │ M2   │  │  REPLICA  (LiteFS read-only)
                 │                                                              │ (RO) │  │  serves reads; write→forward to primary
                 │                                                              └──────┘  │
                 │  Deploy = blue-green: bring up new machine, wait /health/ready,        │
                 │  cut LB over, retire old. Old machine serves the entire warmup.        │
                 └────────────────────────────────────────────────────────────────────────┘

   Self-heal loop:  WA/web ──POST /liveness/beat (real processing-liveness)──▶ brain registry
                    brain: stale/wedged limb ──▶ bounded, rate-limited Fly Machines restart of that limb
```

### The seven components (each = its own R-number under the R-F2813 epic)

1. **Readiness gate** — new `/health/ready` returning 200 only when `llm_provider is not None` **and** RAG reachable. (Intentionally NOT gated on knowledge/neural graphs — chat degrades gracefully without them by design, R-F2201.) Chat handler returns a fast **503 "warming up"** when `get_llm()` is None instead of entering the pipeline. WA/web consult readiness before dispatch. **Prerequisite for blue-green cutover (#4).**
2. **Interruptible writes** — run the writer on a `sqlite3` connection in a controlled threadpool with `set_progress_handler`/`interrupt()`, so a runaway SQL is *cancelled* rather than wedging the thread. Bound the drain (`_WRITE_BATCH_SIZE` + `executemany`) so one tick can't hold the writer for up to 2000 serial round-trips. **Removes "restart-as-recovery"; prerequisite for LiteFS (never replicate a wedge).**
3. **LiteFS state replication** — SQLite replicated: one primary writer, ≥1 read replica. §6-clean (free, self-hosted, keeps SQLite; no Upstash/Postgres). chromadb moved to server/replicated mode or a co-replicated file. **Makes state reachable from ≥2 machines.**
4. **≥2 machines + blue-green deploy** — `min_machines_running=2`; deploy strategy brings the green machine to `/health/ready` (#1) before LB cutover. **Removes deploy=outage and single-machine-death=outage.**
5. **Processing-liveness + brain-side recovery actuator** — WA/web stamp `_lastInboundAt`/`_lastProcessedAt`, POST `/liveness/beat` with *real* liveness (not `!!sock`); brain detects a stale/wedged limb and triggers a **bounded, rate-limited Fly Machines restart** of that surface. **Surfaces self-heal without a human.**
6. **Deploy lease** — a single flock/advisory lock across `deploy.sh`, `deploy.ps1`, and `ci_deploy` so manual + autonomous deploys serialize. **Still required with blue-green** (serializes the pipeline, not just the machine).
7. **Strict reads on integrity paths** — convert false-clean-critical graceful reads (phase-gate keys, cost cap, DD report_index, quarantine, ledger) to `get_strict`/`get_json_strict`. **Removes "store-down reads as empty → false-clean"** (same class as the §1 gate fabrications).

---

## 4. Staged migration plan (safe build order)

Each stage is **independently shippable, reversible, and capability-tested**. No stage begins until the prior stage is verified live. The order is a hard dependency chain: you cannot safely add a second machine (Stage 4) before writes are interruptible (Stage 2) and state replicates (Stage 3), or you replicate a wedge and split-brain the store.

> **Sub-number convention:** each stage ships under the R-F2813 epic with its own reserved child R-number (reserve at stage start per §2). The epic R-F2813 covers this design doc.

### Stage A — Readiness gate + fast-fail (component 1) · risk: LOW
- **Changes:** add `/health/ready` (`routes/aria.py`); chat handler fast-503 when llm is None (`routes/aria.py:10291`); WA `askARIAAsync` + web `ariaProxy` consult readiness and reply "brain is starting, I'll post as soon as it's ready" instead of a 15-min poll / 45s hang.
- **Rollback:** pure-additive endpoint + a guarded branch; revert = remove the branch. No data touched.
- **Capability test:** boot a TestClient with `llm_provider=None` → `/health/ready` = 503 and chat returns fast "warming," not a hang; with llm set → `/health/ready` = 200 and chat serves. WA unit: readiness-503 → friendly reply path, not the 15-min poll.
- **Live verify:** deploy, curl `/health/ready` during the warmup window, confirm it flips 503→200 as llm init completes.

### Stage B — Interruptible writes + bounded drain (component 2) · risk: MEDIUM
- **Changes:** `state_store.py` writer executes on an interruptible `sqlite3` connection in a controlled threadpool; a per-op deadline calls `interrupt()` instead of leaving the thread wedged. `_flush_write_queue` honours `_WRITE_BATCH_SIZE` + `executemany`.
- **Rollback:** env flag `ARIA_INTERRUPTIBLE_WRITES` (default off first deploy → measure → on). Off = current aiosqlite path unchanged.
- **Capability test:** inject a deliberately slow SQL; assert it is `interrupt()`-cancelled within the deadline and subsequent writes proceed **without** an `os._exit`/cold boot (the whole point). Assert the watchdog (`state_store.py:988`) still fires on a *genuine* wedge — i.e. we did not disarm the R-F2277 safety net.
- **Live verify:** measure-mode logs interrupt events; confirm no regression in write latency percentiles; confirm the wedge watchdog still present.

### Stage C — LiteFS single-node (component 3, replication OFF) · risk: MEDIUM-HIGH
- **Changes:** introduce LiteFS as the SQLite layer on the **existing single machine** first (mount, config, `litefs.yml`), writer = primary, no replica yet. Prove the store works identically under LiteFS before adding a second node. chromadb server/replicated-mode decision finalised here.
- **Rollback:** LiteFS mounts the same underlying file; a bad Stage C = redeploy the pre-LiteFS image pointing at `/data` directly. **Take a volume snapshot before this stage** (`flyctl volumes snapshots create`).
- **Capability test:** full boot under LiteFS; `lifespan()` smoke (§9); 907 MB store reads/writes verified; boot time unchanged; the wedge watchdog + WAL safety net intact.
- **Live verify:** build_rev advances; store row counts + a known DD read match pre-migration; 24h soak before Stage D.

### Stage D — Second machine + blue-green (components 4, replication ON) · risk: HIGH
- **Changes:** `min_machines_running=2`; LiteFS replication on (M2 = read replica, write-forward to primary); `fly.toml` deploy strategy = bluegreen keyed on `/health/ready`. Confirm single-writer invariant holds (LiteFS elects one primary).
- **Rollback:** scale back to `min_machines_running=1` + replication off (one flag + one scale command); blue-green → single-replace.
- **Capability test:** replica serves reads while primary redeploys; a forced primary kill → replica/failover keeps serving reads, writes fail-forward gracefully; a deploy shows **zero** LB downtime (readiness cutover). Assert no split-brain: two machines, one writer.
- **Live verify:** deploy a no-op change and watch WA/web stay up throughout (the acceptance criterion for "deploy is no longer an outage").

### Stage E — Surface self-heal (component 5) · risk: LOW-MEDIUM
- **Changes:** WA/web POST `/liveness/beat` with real processing-liveness; brain `check_stale_and_gap` gains a **bounded, rate-limited** Fly Machines restart actuator per surface (not just a gap). WA local watchdog gains a processing-liveness probe + `keepAliveIntervalMs`; R-F1515 fast-path hard 2s cap.
- **Rollback:** actuator behind `ARIA_SURFACE_AUTOHEAL` (default off → observe → on); beats are additive.
- **Capability test:** simulate a WA zombie (connected, no inbound) → brain detects stale → issues exactly one rate-limited restart → WA reconnects; assert the actuator cannot loop (rate cap). Assert a healthy WA is never restarted (no false-positive).
- **Live verify:** induce a controlled WA stall in a window; confirm auto-recovery without manual `flyctl apps restart`.

### Stage F — Deploy lease + strict reads + telemetry (components 6, 7 + log fix) · risk: LOW
- **Changes:** flock across all deploy paths; `get_strict` on the integrity keyset; designed-shed logs → INFO "shed to WAL."
- **Rollback:** all independently revertible; strict reads behind per-callsite change (surgical, one keyset).
- **Capability test:** two concurrent deploys → second blocks on the lease, no SIGTERM race; a store-down injection on a phase-gate read → raises/So the gate reads `unknown`, never a fabricated pass; shed event logs at INFO not WARNING.

---

## 5. §6 compliance + risk register

**§6 (native, no paid persistence):** LiteFS is free + self-hosted (mirrors the "files + LLM only" rule — it *is* the SQLite file, replicated). No Upstash, no Postgres, no paid provider. chromadb stays self-hosted. ✅ compliant.

| Risk | Likelihood | Mitigation |
|---|---|---|
| LiteFS migration corrupts/loses the 907 MB store | Low | Volume snapshot before Stage C; Stage C is single-node (no topology change); 24h soak before Stage D; row-count + known-read verification |
| Split-brain (two writers) under LiteFS | Low | LiteFS elects exactly one primary; Stage D capability test asserts single-writer; write-forward from replica |
| Blue-green doubles boot cost / cold-boot storm | Med | Readiness-gated cutover; deploy lease serializes; heavy graphs already deferred (R-F2122) |
| We disarm the R-F2277 wedge watchdog while making writes interruptible | Med | Stage B capability test explicitly asserts the watchdog still fires on a genuine wedge |
| Surface auto-heal actuator loops / thrashes a healthy limb | Med | Rate-cap + false-positive test in Stage E; default-off observe-first |
| Scope creep into brain reasoning / DD | Low | This epic is availability-only; no change to verifier/DD/reasoning paths |

**Estimated shape (not a commitment):** Stages A, F are days each (code-local). B, E are ~a week each with soak. C, D are the heavy lift (LiteFS + HA) — multi-week with mandatory soak windows. Total: a multi-week program, deliberately staged so value lands incrementally (Stage A alone ends the 15-min hang) and every stage is reversible.

---

## 6. Acceptance criteria (definition of "bulletproof")

1. A deploy of a 1-line change causes **zero** WA/web downtime. *(Stage D)*
2. A single machine death causes **zero** read downtime and graceful write-failover. *(Stage D)*
3. A runaway SQL is cancelled in-process — **no cold boot** as recovery. *(Stage B)*
4. A WA zombie auto-recovers in <2 min with **no human**. *(Stage E)*
5. A booting brain returns an instant honest "warming," never a 15-min hang. *(Stage A)*
6. A store-down condition can **never** read as "empty" on an integrity path. *(Stage F)*
7. Every preserved-behaviour item in §1 still holds (asserted per stage).

---

## 7. Open questions for the operator

1. **chromadb under HA** — replicate the chroma file via LiteFS too, or run chroma in server-mode as a separate co-located process? (Affects Stage C scope.) Recommendation: replicate via LiteFS if the corpus fits the single-writer model; else server-mode.
2. **Machine sizing at 2×** — two `shared-cpu-4x` machines double the ~$/mo baseline. Confirm the HA spend is approved (still well under any external-persistence cost). 
3. **Soak windows** — Stages C/D each want a 24h soak. Confirm we hold between stages rather than chaining them.

---

## 8. Next action

On sign-off of this design: reserve the Stage-A child R-number and implement the **readiness gate** first — it is the lowest-risk stage, the prerequisite for blue-green, and it alone converts today's 15-min hang into an instant honest "warming." Each subsequent stage proceeds only after its predecessor is verified live (§3 verify-after-fix, §9 lifespan smoke, §11 deploy-verify).
