# ARIA Brain — Sequenced Scaling Design (Phase 0 → 2)

**Status:** design / handoff. Read-only synthesis — no code changed by this doc.
**Author:** Claude (review lane). **Date:** 2026-07-11. **Owner of execution:** state-store / auth lane.
**Fuses:** the live 360 review (aria-intel) + the "prepare the brain for scale" vision into ONE plan.

> **Grounding rule:** every item below is anchored to a verified `file:line` or a live log line
> captured 2026-07-11 15:43–15:46Z. Claims with no anchor are labelled PROPOSED. No fabrication.

---

## 0. The thesis (why this ordering)

The USP is **fast, honest, decision-grade DD**. The single fact that dominates everything:
**one aiosqlite writer + a ~1 GB DB serves both the hot path and the heavy background**, so a
background ingest can stall a user's DD request 17–18 s. The 360 findings are **not separate
bugs — they are the load-bearing prerequisites for scale.** You cannot scale a wedging
foundation; you amplify the failure. Therefore **Phase 0 = the 360 review**, and only then Phase 1+.

### Live evidence (verified 2026-07-11, aria-intel logs)
- `continuous_profiler`: **36.5 % of event-loop time in `aiosqlite/core.py:_connection_worker_thread`**; heartbeat "stale for 4.8 s … event-loop stall (#26,#27)".
- `state_store`: `get(...) timed out after 5s — DB may be bloated or under WAL recovery`; `[R-F2277] liveness probe FAILED — unavailable for 17s / 18s`; `RECOVERED after 17s`.
- Internal self-calls **401**: `POST /api/aria/report`, `GET /api/aria/self/staged`, `GET /api/aria/cost/monthly/status`, `GET /api/aria/autonomous/status`, `GET /api/aria/dd/watchlist/alerts/unread-count` (all `127.0.0.1`/`localhost:8000`).
- `capability_gaps`: `Unknown gap type 'llm_provider_failure' — recording anyway` (repeated).
- RunPod `…runpod.net/v1/models` → 404 (sovereign shadow endpoint stale).
- On disk: `aria_state.db` **981 MB**, `aria_knowledge_store.db` 667 MB, `aria_search.db` 97 MB.

---

## Dependency map (the reason for the sequence)

| Scaling move | Depends on (360 finding) | Failure if skipped |
|---|---|---|
| Knowledge sub-brain / writer split | **IS** #1 | — (this is where stabilize == scale) |
| Parallelize ingest | #1 | N ingest workers on ONE writer make #1 worse |
| Budget the brain (quotas) | #2 (401 proprioception) | Can't allocate what you can't measure |
| LLM task-value routing | #3/#4 (taxonomy, provider) | Can't route/fail over on unrecognised failures |
| Multi-machine role-split | #1 (needs concurrent store) | Splitting processes just relocates contention |
| More autonomous throughput | #5 (worker respawn) | Load onto a near-ceiling supervisor compounds |

---

## PHASE 0 — Stabilize the foundation (nothing scales until this holds)

### P0.1 — State-store contention (HIGH, the keystone) — *state-store lane*
**Current reality (verified):** single writer `state_store.py:109 (_conn)`; documented "907 MB
single-writer ceiling" at `state_store.py:424`; **the DB is 981 MB — already over the ceiling.**
The hot/cold split is **BUILT but DISABLED**: `state_store.py:439 (_HOTCOLD_SPLIT, default off)`,
cold connections `:442-443 (_cold_conn/_cold_read_conn)`, key router `:472 (_writer_queue_for)`.
Prior work: liveness watchdog (R-F2277), WAL passive-checkpoint `state_store.py:151`, and the
hot/cold design in `memory/state_store_writer_ceiling_design_awaiting_approval_2026_07_02.md`
which lists the remaining steps: **0b scan-union + delete-routing + router-tighten + cold-sweeper
→ 1 backfill → 2 cutover → 3 reclaim (VACUUM in a quiet window).**

**So this is "safely finish + enable what exists," not a rewrite.** The flag is dark because a
scan reads hot-only and would blind `verified_facts` — that scan-union is the gating fix.

**Do:** complete step 0b (scan-union so the flag is safe to flip), backfill cold prefixes
(`audit:by_hash`, `verified_facts`, `verified_intel:fact`, `reasoning_library`), cutover, then
reclaim. The knowledge/ingest write load then lands on the COLD writer; the hot path (chat, DD
status, watchlist, cost) keeps a small fast HOT writer.
**Acceptance:** live `aiosqlite` loop-time share < 10 % (from 36.5 %); zero `liveness FAILED`
windows > 2 s over a 30-min sample; `state_store.get` p99 < 500 ms; `aria_state.db` (hot) shrinks
toward < 200 MB after reclaim.

### P0.2 — Restore proprioception (HIGH) — *auth/routes lane*
**Current reality (verified):** auth dependency 401s when `ARIA_API_TOKEN` is set and the caller
omits it (`routes/aria.py:259-263` soft-rollout design; `:274 _aria_api_token`). An internal token
mechanism **already exists**: `routes/aria.py:277 (_aria_internal_token)`, `ARIA_INTERNAL_TOKEN`.
The internal workers making the self-calls simply **aren't attaching a token** → 401.
**Do:** have internal/localhost self-calls send `ARIA_INTERNAL_TOKEN` (Bearer), and accept it in
the auth dependency alongside `ARIA_API_TOKEN`. Infra exists; wire the callers + widen the check.
**Acceptance:** zero `401` on the five internal endpoints in a 30-min live sample; `report`,
`cost/monthly/status`, `autonomous/status`, `self/staged`, `watchlist/alerts/unread-count` return
2xx to internal callers. **This is a hard dependency for P1 budgeting.**

### P0.3 — Provider-routing honesty (MED) — *autonomy/LLM lane*
**Current reality (verified):** `capability_gaps.py:45 (VALID_GAP_TYPES frozenset)` does not
include `llm_provider_failure`; `:244-245` logs "Unknown gap type … recording anyway". RunPod
`/v1/models` 404 = sovereign shadow endpoint stale.
**Do:** add `llm_provider_failure` (or remap to the intended existing type) in `VALID_GAP_TYPES`
`capability_gaps.py:45`; confirm the DeepSeek fallback path serves cleanly when the sovereign
endpoint 404s (no user turn silently degraded); mark the stale RunPod endpoint down for discovery.
**Acceptance:** no "Unknown gap type" warnings for provider failures; a forced sovereign-404
still returns a grounded DeepSeek answer with the failure recorded as a first-class gap.

### P0.4 — Worker/supervisor health (MED) — *main/bg lane*
**Current reality:** bg supervisor `main.py:48 (_bg_task)` + respawn; the 360 saw `seed_knowledge`
reach 5/5 respawns (near the `_BG_MAX_RESPAWNS = 5` ceiling, `main.py:40`).
**Do:** confirm whether seed_knowledge is now stable or chronically dying; if chronic, root-cause
(don't just raise the ceiling — §1 root-cause rule).
**Acceptance:** no bg loop reaches its respawn ceiling over a 24-h window.

---

## PHASE 1 — §6-preserving scale (only once Phase 0 holds)

### P1.1 — Parallelize ingest (depends on P0.1)
**Current reality (verified):** `brain_ingest_queue.py:1` — "durable SQLite priority queue … single
background drain worker"; bound `:56 (_QUEUE_MAX=50000)`; drain loop `brain_hook.py:1057`. Separate
DB already (proves the split pattern).
**Do:** partition the queue into N parallel drains by priority/domain, feeding the COLD writer
(safe only after P0.1 isolates it from the hot writer).
**Acceptance:** ingest throughput scales ~linearly with drain count with zero hot-path p99 regression.

### P1.2 — Budget the brain (depends on P0.2)
**PROPOSED.** Give each subsystem (autonomous engine, coder, research, ingest, screening) an
explicit quota: writer-ops/sec, LLM $/hr (within the §17 $300 cap), CPU share. Enforced via the
proprioception signals restored in P0.2 + the accurate wiring surface (R-F2537 `get_stats` `coverage`,
R-F2548 audit). Autonomy then compounds value instead of contending.
**Acceptance:** under synthetic load, background work never pushes hot-path p99 above target.

### P1.3 — LLM task-value routing (depends on P0.3)
**PROPOSED.** Route cheap/high-volume turns to a cheap/local model; reserve the strongest model for
grounded decision-grade DD (honesty = product). Sovereign-7B slots in for volume *once it clears the
objective grounding bar* (it did not, per the 2026-07-11 objective eval — see
`memory/honesty_layer_and_sovereign_verdict_2026_07_11.md`).

### P1.4 — Tiered retrieval (extends existing)
**Current reality (verified):** RAG is a real vector index — `rag_store.py:11` (chromadb persistent),
hot/cold collections `:105 (aria_documents_cold)`. Neural graph `neural_memory.py:46 (EDGES_KEY)`
sharded `:66-72 (R-F2082, ~2.4M edges)`.
**Do:** formalize hot/warm/cold retrieval tiers so latency + relevance stay flat as knowledge 10×'s;
pair with the shipped citation-verification so more knowledge = better-cited, not noisier.

---

## PHASE 2 — Inflection decisions (operator-gated; reach only if P1 isn't enough)

- **P2.1 — §6 persistence inflection.** Files-only hits a ceiling at ~single-machine / single-writer-
  per-store (we're at it: 981 MB > 907 MB). If P1 partitioning proves insufficient: self-hosted
  **Postgres on a fly volume** for hot state (not external SaaS → arguably within §6's spirit) and a
  **dedicated vector DB** for RAG. §6's "burden of proof on new persistence" is now MET by a recurring
  SEV-1. **Operator decision.**
- **P2.2 — Multi-machine role-split.** Enabler exists but is OFF: `main.py:77-104 (ARIA_ROLE
  engine/web/all, default 'all')`, election `_elect_engine_role` `main.py:169`, singleton gate
  `_runs_singletons`. N stateless web workers + 1 singleton engine + shared concurrent store.
  **Requires P2.1** (a concurrent store) or it just relocates contention. **Operator decision.**
- **P2.3 — Restore automated ci_deploy.** Orthogonal but real: `deploy-fly.yml` push trigger was
  removed (R-F1408) because `FLY_API_TOKEN` is stale. Rotate the token + re-enable the trigger.
  **Operator-only** (GitHub secret).

---

## What "done" looks like
Ingestion is a parallel, back-pressured pipeline into a **tiered memory**; retrieval stays fast +
well-cited at any knowledge size; the **decision-grade hot path is structurally isolated** and never
held hostage by background work; autonomy is **budgeted**; LLM cost is **routed** by task-value; and
**proprioception** makes every tier self-observing so ARIA heals degradation before a user feels it.
More data → a bigger moat, not more wedges.

## Boundaries
P0.1/P0.2 are the state-store/auth lane's to execute (do not double-edit). This doc is the grounded,
sequenced handoff. Operator calls: P2.1 (§6 persistence), P2.2 (multi-machine), P2.3 (ci_deploy token).
