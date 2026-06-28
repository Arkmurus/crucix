# ARIA Scaling Readiness Plan (1 → 100+ users)
**R-F2098 · 2026-06-28 · grounded by a read-only 4-step verification (file:line evidence below).**

> Posture: today's stack is correct for **1–3 users** after the recent stability fixes. This plan stages the path to many users so each step is **reversible, triggered by a real signal, §6-compliant (self-hosted, no paid persistence), and Phase-A-aware** (heavy scale-out is Phase B — execute behind gate closure / operator override; the cheap reliability/quota items are operational and safe now).

---

## 1. Verified current architecture (the real scope)
- **One uvicorn worker, one event loop** — `main.py:4116` (no `workers=`). VM `shared-cpu-4x` / 8GB, **single machine**, `min_machines_running=1`, `/data` fly volume **NOT shared across machines** (`fly.toml:122-132`).
- **~19 background singletons** gated by the per-process `ARIA_ROLE` env (`main.py:85-103`) — **not** leader election. `uvicorn --workers N` ⇒ N copies of every singleton (N× cost / N× external calls / races) OR `ARIA_ROLE=web` ⇒ zero engine.
- **State all on the per-machine `/data` volume:** `state_store` SQLite-WAL (`state_store.py:680`), chromadb PersistentClient (`rag_store.py:243`). `redis_store` is a **SQLite shim** — no real Redis (`redis_store.py:49,84`; Upstash cancelled).
- **Heavy work (DD/doc/research)**: decoupled from the HTTP request (job_id + poll + R-F1413 callback) but **runs on the API event loop**, no worker pool. Admission caps **3 inline / 3 deep-bg**, **per-process** (`routes/aria.py:6376,6385,6415`).
- **encode_offload**: per-process 1-worker ProcessPool, N model copies at N workers (~0.1–0.2GB each — cheap; `encode_offload.py:89`).
- **LLM**: multi-provider chain EXISTS but only DeepSeek active; Groq/OpenAI/Gemini/Anthropic auto-join **if their key env is set** (`fallback.py:637`); Anthropic also needs `ARIA_ANTHROPIC_ENABLED=1`.
- **Cost/quota**: $300/mo cap is **check-then-fire-and-forget, 30s-stale, per-machine** (`metered.py:110-148`, `cost_tracker.py:697`). Per-user **$20/mo + 30 RPM LIVE**; per-user **daily $5 = DEAD** (`user_quota.record_cost` never called). Autonomous daily $50 live but covers only scheduled tasks.
- **Watchdog/boot**: R-F703 in-loop stall detector + R-F1417 off-loop hard-wedge `os._exit(1)` self-restart + R-F1421 boot isolation — all present (`main.py:208,872,968`).

## 2. The real ceilings, in the order you hit them
1. **LLM cost + single-provider SPOF** (not infra). $300/mo blows in hours at ~10 active DD users; DeepSeek outage = total outage today.
2. **Per-process cost/quota correctness** — caps multiply per machine, not atomic; dead daily cap.
3. **Per-machine `/data` state** — SQLite + chromadb can't span machines (hard block to horizontal).
4. **Per-worker RAM hydration (~3GB)** — blocks even single-machine `workers=2` on 8GB.
5. **Per-process heavy-work caps** — 3 inline/3 deep multiply ×workers, weakening R-F2055/56/58.
6. **Event-loop saturation** (mostly mitigated by the recent encode-offload + defer fixes; role-split finishes it).

## 3. Phased plan (each phase: trigger · prerequisites · reversibility)

### Phase 0 — Robustness NOW (do at 2–3 users; protects at ANY N; cheap + reversible)
*All operational/reliability — safe under Phase A. Several are ARIA's brain lane (coordinate / Gap).*
- **0.1 Wire the dead per-user daily cost cap** (`user_quota.record_cost` into the post-call accounting) OR delete the false "live backstop" comments. **#1 guardrail** — one heavy user must not bankrupt/starve the budget. *(routed to ARIA)*
- **0.2 Make the $300 cap pre-spend/reserve** (not check-then-fire-and-forget) to bound concurrent overshoot. *(ARIA)*
- **0.3 Cost alerting** at 50/80/90% MTD to the operator channel (you already watch it; automate it).
- **0.4 Wire a 2nd LLM provider, dormant→on** — set `GROQ_API_KEY` (free tier) and/or `ANTHROPIC_API_KEY`+`ARIA_ANTHROPIC_ENABLED=1`. **1–2 secrets, zero code** → removes the DeepSeek SPOF. *(operator secret decision)*
- **0.5 Load-test harness** (k6/locust: 10→50→100 concurrent chats + DDs) → **measure the real knee** instead of guessing. Repeatable, gates every later phase.
- **0.6 Keep topology honest**: do NOT set `uvicorn --workers N` or `fly scale count >1` yet — both diverge/duplicate today (§1). Document the single-machine constraint in the deploy runbook.

### Phase 1 — Externalize coordination state (prerequisite for ALL multi-process/machine work)
- **Trigger:** load test shows event-loop or write contention at target concurrency, OR you commit to >1 worker/machine.
- **Do:** stand up **self-hosted Valkey/Redis** on a dedicated fly machine (§6-compliant: self-hosted, not paid SaaS — **operator §6 decision**), flip `ARIA_STATE_BACKEND` to it (the `redis_store` aioredis path already exists). Move to it: cost/quota counters (atomic!), rate-limit token bucket, OCR/chat/readdoc job stores, the **DD heavy-work caps** (3 inline/3 deep → shared atomic), per-user fairness locks.
- **Why first:** every later step (shared cost cap, multi-worker, multi-machine) depends on shared coordination. Reversible: flip the env back to sqlite.

### Phase 2 — Role-split: 1 engine + N web on ONE machine
- **Trigger:** sustained >3–5 concurrent users or health-stalls after Phase 1.
- **Do:** ship **R-F2090** (reserved) — leader-elected singletons so exactly one `engine` process runs the ~19 loops while `web` processes only serve. Requires Phase 1 (shared state) + solving per-worker RAM: either a bigger machine OR an **engine-owns-data / web-queries-it seam** (invasive — every in-process `knowledge.*`/`rag_store.*`/chromadb call becomes a client call). **Set `ARIA_TOTAL_LLM_WORKERS` to the real process count** (or use the Phase-1 shared token bucket).
- **Reversibility:** env flip back to single `all` role.

### Phase 3 — Horizontal (multiple machines) + decouple heavy work
- **Trigger:** one 4-CPU machine saturates after Phase 2.
- **Do:** move RAG to **chromadb client/server** (or self-hosted Qdrant/pgvector) so machines share one corpus; stateless `web` machines behind fly autoscale (set explicit `[http_service.concurrency]`); a **dedicated embedding service** (extract `encode_offload`'s worker behind the existing `_safe_encode` seam — clean retrofit); a **job-queue worker pool** for DD/doc so a 100s job never ties a web worker (invasive: serialize llm/contextvars/caps into job payloads; self-hosted broker per §6).
- **Reversibility:** scale count back to 1.

### Phase 4 — LLM resilience at load
- **Trigger:** approaching budget/throughput limits with real users.
- **Do:** multi-provider routing (Phase 0.4 generalized), a **budget that matches the user count** (operator decision — $300 is a hobby budget), enforced per-user quotas (Phase 0.1 + Phase 1 shared), and a *semantic* response cache (the current `LLMResponseCache` is near-useless — volatile context in the key, no streaming).

## 4. Cross-cutting (every phase)
- **Observability:** per-request latency/outcome (§25 exists), cost-per-user dashboard, SLOs, the Phase-0.5 load test as a gate before each phase.
- **Wedge semantics per topology:** R-F1417 `os._exit(1)` assumes one-process=one-machine; redefine for multi-worker/multi-machine.
- **Rollback:** every phase is an env flip / scale-count change back to the prior topology.

## 5. Decision points for the operator
- **Budget ceiling for testing** (drives how far to build + whether a 2nd provider is needed now).
- **§6 stance** — self-hosted Valkey/Postgres/chromadb-server is the §6-compliant path; confirm self-hosting vs revisiting the no-paid-persistence rule at scale (cost/effort tradeoff).
- **2nd LLM provider** — Groq (free) and/or Anthropic top-up.
- **Target user count + cadence** — sets the trigger thresholds; **load-test to find the real knee** before building beyond Phase 0/1.

## 6. Minimum to be "prepared" for 2–3 testers right now
Phase **0.1** (per-user cap) + **0.3** (cost alert) + **0.4** (2nd provider dormant) + **0.5** (load test). That makes you safe + prepared **without over-building** — everything beyond is triggered by the load test, not assumption.

---
*Verification agents (read-only, 2026-06-28) corrected three of the original assumptions: there is no Redis today (SQLite shim) and paid is §6-barred; a 2nd LLM provider is 1–2 secrets not a build; the multi-worker OOM driver is ~3GB per-worker data hydration, not the embed model. Single machine / single worker remains the only correct topology until Phase 1 lands.*
