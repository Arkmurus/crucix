# state_store 907 MB single-writer ceiling — design (for operator approval)

**Status:** DESIGN ONLY — no code written. Awaiting operator go/no-go before implementation.
**Author:** Claude (2026-07-02). **Context:** follow-up (b) from the 2026-07-02 wedge incident ([[incident_state_store_wedge_2026_07_02]]). The R-F2277 watchdog now *self-heals* a wedge; this addresses the *structural cause* that makes wedges likely and writes slow.

## 1. Problem (evidence, measured live 2026-07-02)

`/data/aria_state.db` = **910 MB**, **661,680 rows**, **75% (496k) have NO TTL**. All writes funnel through **ONE aiosqlite writer connection** (`_conn`) on **one worker thread** behind **one asyncio.Lock** (compound ops). Row distribution by key prefix:

| rows | no-TTL | prefix | nature |
|---|---|---|---|
| 158,266 | 0 | `crucix:aria:cost` | HOT, churny, ephemeral (all TTL'd) |
| 129,135 | all | `crucix:audit:by_hash` | COLD, permanent audit hash-chain |
| 117,468 | all | `aria:verified_facts:GENERAL_CLAIM` | COLD, permanent knowledge |
| 104,924 | all | `crucix:verified_intel:fact` | COLD, permanent knowledge |
| 99,423 | all | `crucix:audit:by_entity` | COLD, permanent audit index |
| 19,486 | all | `crucix:pending_actions:by_id` | warm, accumulating |
| 11,006 | 8,742 | `crucix:aria:coder` | warm |
| … | | (gap_claim, outcomes, chat_audit, conv, trace) | HOT/operational |

**Two independent problems:**
- **(P1) Single-writer serialization.** Every write — a hot cost increment, a heartbeat, a gap record — queues on the *same* thread as a cold 100 KB fact write against a 910 MB file. One slow op (large write, WAL checkpoint, lock contention) stalls **all** writes. This is the mechanism behind the 3.5 h wedge and the routine `state_store.get timed out` warnings under L3 autonomy.
- **(P2) Monolithic growth.** ~450k rows (68%) are **cold, permanent, append-only** reference data (audit chains 228k + verified facts/intel 222k). They never expire (correct per §7 infinite-memory) but they bloat the *same* file the hot path writes to — inflating WAL churn, checkpoint cost, and page-cache pressure for every hot write.

## 2. Root cause

The hot operational write path and the cold permanent knowledge store share **one file + one writer connection**. SQLite is single-writer *by design* (only one writer at a time in WAL), so the fix is **not** "more writer connections to the same file" (that just yields `database is locked`). The fix is to **separate the hot path from the cold bulk** so they don't contend.

## 3. Options considered

| Option | Verdict | Why |
|---|---|---|
| **A. Writer connection pool (same file)** | ❌ Reject | SQLite allows one writer at a time; a pool → `database is locked`, no throughput gain. |
| **B. External DB (Redis/Postgres)** | ❌ Reject | Violates §6 (native, file-only; no paid persistence). Upstash already cancelled. |
| **C. Hot/cold DB split (two files, two writer conns)** | ✅ **Recommend** | Cold permanent data → its own file+conn; hot operational data stays in a small, fast file. Independent writer threads → a cold write can't stall a hot write. Native/file-only (§6). Preserves all data (§7). |
| **D. Growth controls only (TTL/prune/aggregate cost)** | ◐ Necessary but insufficient | Bounds P2 growth but leaves P1 (single-writer contention) intact. Do it *alongside* C. |
| **E. Periodic VACUUM + WAL tuning** | ◐ Complementary | Reclaims space, caps WAL; doesn't fix contention. Low-risk add-on. |

## 4. Recommended design — hot/cold split (Option C + D + E)

**C1. Two SQLite files, each its own writer connection + write-queue/worker:**
- `aria_state.db` (**HOT**) — operational/ephemeral: cost, gaps, outcomes, coder, heartbeats, dedupe, traces, chat_audit, pending_actions. Stays small (~200k rows, mostly TTL'd) → tiny WAL, sub-ms writes, negligible wedge surface.
- `aria_knowledge_store.db` (**COLD**) — permanent append-only: `audit:by_hash`/`by_entity`, `verified_facts:*`, `verified_intel:*`, reasoning_library. Large but write-rarely; its own thread, so a big fact write never blocks the hot path.

**Routing:** a pure `_route_db(key) -> HOT|COLD` classifier by key-prefix (unit-testable, the *only* new decision point). Reads try the routed DB; a short dual-read fallback during migration covers keys written before the split. The existing read-pool (R-F2242), write-coalescing (R-F2157), watchdog (R-F2277), and expiry sweeper (R-F2154) are duplicated per-file (cheap).

**D. Growth controls (do together):**
- `crucix:aria:cost` (158k) — confirm the sweeper actually prunes it; if TTL is 90 d, evaluate shortening or rolling-up daily aggregates (a cost record per call × L3 autonomy = the fastest-growing hot prefix).
- pending_actions (19k, no TTL) — audit whether resolved actions should expire.
- Cold prefixes stay permanent (§7) — they just live in the cold file.

**E. Maintenance:** WAL `wal_autocheckpoint` already tuned (R-F2137); add a low-frequency `VACUUM` (or `PRAGMA incremental_vacuum`) on the cold file during a quiet window to reclaim deleted-page space.

## 5. Migration plan (online, reversible)

1. **Phase 0 (flagged, default OFF):** ship the two-file plumbing + `_route_db` behind `ARIA_STATE_HOTCOLD_SPLIT=0`. No behaviour change. Unit-test the router + per-file workers.
2. **Phase 1 (backfill):** background one-time copy of cold-prefix rows from `aria_state.db` → `aria_knowledge_store.db` (idempotent, resumable, rate-limited so it can't itself saturate the writer). Dual-read during backfill.
3. **Phase 2 (cutover):** flip `ARIA_STATE_HOTCOLD_SPLIT=1`. Cold writes go to the cold file; hot file stops growing with permanent data. Monitor write latency + wedge count.
4. **Phase 3 (reclaim):** once cold rows are confirmed migrated + read-verified, delete them from the hot file and `VACUUM` it → hot file drops from 910 MB to ~tens of MB. **This is the payoff:** the hot write path now operates on a small file.
5. **Rollback:** set the flag to 0 at any phase → reverts to single-file reads/writes (cold file becomes a harmless superset copy). No data loss at any step (§7).

## 6. Blast radius & risks

- **HIGH blast radius** — state_store is the spine; every module reads/writes it. Hence the flag-gated, phased, dual-read, reversible plan and a full capability-test pass (§3c) driving real read-after-write across both files before cutover.
- Risk: a key written to the wrong file → read-miss. Mitigated by dual-read fallback + the router being a small pure tested function + a reconciliation check in Phase 2.
- Risk: backfill saturating the writer (the very thing we're fixing) → rate-limited, runs on the cold file's own thread, pausable.
- Lifespan smoke + the R-F2277 watchdog both apply per-file.

## 7. Effort / phasing

~1 focused session for Phase 0 plumbing + router + tests; Phase 1–2 backfill/cutover monitored across a following session; Phase 3 reclaim once stable. Each phase is its own R-number, its own 4-step review + smoke, and independently shippable/reversible.

## 8. Recommendation

Proceed with **C + D + E**, flag-gated and phased. It is the only option that removes the single-writer contention (P1) *and* stops the hot path paying for cold-data growth (P2) while staying native/file-only (§6) and lossless (§7). Await operator **go/no-go** before Phase 0.
