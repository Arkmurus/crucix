# Dependency Minimisation — ARIA Mirrors Claude
**Strategic anchor · 2026-05-11**

The directive: ARIA's architecture must converge on Claude Code's. If
Claude Code doesn't depend on it, ARIA shouldn't either. Robust
independence is the bar. This doc codifies the migration path and the
specific code that R-F235 + R-F236 ship to make it real.

Pairs with: `aria_mirrors_claude.md` (auto-memory feedback rule),
`docs/aria_independence_roadmap.md` (sovereign-LLM strategic anchor),
`docs/verification_protocol_2026_05_11.md` (how every commit ships).

---

## 1. The architectural baseline — what Claude Code uses

Reduced honestly, Claude Code's stack is:

```
Claude Code = LLM + files + on-demand web tools
              │     │       │
              │     │       └─ WebSearch / WebFetch when invoked
              │     │           (no persistent subscription)
              │     └─ ~/.claude/projects/<repo>/memory/*.md
              │       (plain text files)
              └─ Anthropic-hosted inference
                  (the only paid dependency)
```

No Redis. No Brave. No vector store. No SQLite. No proprietary anything
between the model and the operator's filesystem.

## 2. ARIA today vs ARIA target

| Layer | Today | Target (Claude-Code-equivalent) |
|---|---|---|
| Memory facts | `/data/aria_knowledge.json` (✓ already files) | unchanged |
| Audit log | Redis chain | append-only JSONL on disk |
| RAG | `/data/aria_rag/` chromadb (✓ already disk) | unchanged |
| Queues | Redis lists | SQLite (R-F235) |
| Counters | Redis ints | SQLite (R-F235) |
| Sorted sets | Redis zsets | SQLite (R-F235) |
| Search | Brave + 6 free backends | 6 free backends only (R-F236) |
| LLM | Anthropic | Anthropic → ARIA-LLM (Phase 4 of independence roadmap) |
| Backups | `/data/aria_backups/` (✓ R-F224 covers /data) | unchanged |

Three changes get us 95% of the way there:
1. R-F235 — SQLite backend for Redis-equivalent state
2. R-F236 — Brave opt-out flag
3. Phase 4 — ARIA-LLM sovereign inference (multi-month, separate plan)

---

## 3. R-F235 — SQLite state backend

**Module**: `aria_service/intel/state_store.py`

Mirrors the entire `redis_store` API surface against `aiosqlite`.
Single table (`state`) with kind-discriminated rows; collections
(lists, zsets, hashes) stored as JSON blobs in the same row's `value`
column. TTL is lazy-expired on read AND swept by a background task.

**Dispatcher**: `aria_service/intel/redis_store.py`

Every public function in `redis_store` now checks `ARIA_STATE_BACKEND`
(env var) and routes to `state_store` when set to `sqlite`. The legacy
Upstash path stays intact for backwards compatibility AND so the
migration script can read from both at once.

**Env vars**:
- `ARIA_STATE_BACKEND` — one of `upstash` (default), `sqlite`, `memory`
- `ARIA_STATE_DB_PATH` — SQLite file path (default `/data/aria_state.db`)

**Performance notes**:
- aiosqlite on the same machine: sub-millisecond reads, ~1-2ms writes
- vs Upstash network round-trip: 20-80ms typical
- Switching to sqlite is a NET LATENCY IMPROVEMENT, not just a cost cut

**What the migration script does**:

`scripts/migrate_state.py` reads every key from the current Upstash
backend, detects its type via Redis TYPE, and writes the same data to
SQLite with TTL preserved. List ordering is preserved (lpush in
reverse). Sorted-set scores preserved. Hash fields preserved.
A manifest lands at `/data/aria_backups/state_migration_<ts>.json`
with full audit trail.

---

## 4. R-F236 — Brave opt-out flag

**Module**: `aria_service/intel/web_search.py`

New env: `ARIA_BRAVE_DISABLED=1` short-circuits the Brave backend
without requiring `BRAVE_SEARCH_API_KEY` to be unset. The key stays
configured for the migration window while search routes entirely
through the free backends. Once the subscription is cancelled, the
env var can be removed.

**What still works without Brave**:
- Google News RSS (free, no auth)
- Bing News RSS (free, no auth)
- DuckDuckGo (rate-limited free)
- 7 dedicated source adapters: OFAC SDN, FCDO sanctions, UN SC,
  SEC EDGAR, World Bank, ACLED, BIS Entity List
- 34 RSS feeds (defence press, regulators, think tanks)
- Academic APIs: Semantic Scholar, OpenAlex, CrossRef
- SearXNG self-host when `SEARXNG_URL` is set (R-F183)
- Wayback / archive.is fallback chain (R-F126, R-F187 widened)

The R-F189 capability-gap monitor surfaces a brain_hook absorb if all
general-web backends collapse (so the operator sees the rare degraded
case before users notice).

---

## 5. Migration procedure (single-instance fly.io)

**Pre-flight (on fly.io shell)**:

```bash
# 1. Confirm SQLite is available in the runtime
fly ssh console -a aria-intel
python3 -c "import aiosqlite; print(aiosqlite.__version__)"
```

If aiosqlite is missing, add `aiosqlite>=0.19` to `requirements.txt`
and redeploy first.

**Migration**:

```bash
# 2. Dry-run (counts + sample, no writes)
fly ssh console -a aria-intel
cd /app
python scripts/migrate_state.py --dry-run

# 3. Real migration
python scripts/migrate_state.py

# 4. Verify SQLite has data
sqlite3 /data/aria_state.db "SELECT COUNT(*) FROM state"
```

**Switch over** (atomic):

```bash
fly secrets set -a aria-intel ARIA_STATE_BACKEND=sqlite
# Machine restarts; redis_store.connect now dispatches to SQLite.
```

**Confirm**:

```bash
fly logs -a aria-intel | grep "state_store: SQLite ready"
# Should show:  state_store: SQLite ready at /data/aria_state.db (WAL mode)
# Should NOT show:  Redis connected (Upstash backend)
```

**Cancel Upstash** (after 7 days of clean sqlite operation):

```bash
fly secrets unset -a aria-intel REDIS_URL
# Optionally cancel the Upstash subscription via their console.
```

**Cancel Brave** (whenever):

```bash
fly secrets set -a aria-intel ARIA_BRAVE_DISABLED=1
# Or eventually:
fly secrets unset -a aria-intel BRAVE_SEARCH_API_KEY
```

---

## 6. Cost impact

| Item | Today (monthly est.) | After |
|---|---|---|
| Upstash Redis | ~$10-30 (free tier exhausted) | $0 |
| Brave Search | ~$10-50 (depending on plan) | $0 |
| fly.io machine | ~£15-25 (unchanged) | unchanged |
| fly.io volume | ~£1 (unchanged) | unchanged |
| Anthropic API | variable | unchanged until Phase 4 |
| **Total recurring** | **~£40-95** | **~£16-26** |

The save isn't dramatic in absolute terms but the strategic point is:
ARIA's recurring cost is now bounded by hosting + LLM. Every other
piece is files on disk.

---

## 7. Failure modes + recovery

**SQLite file corruption**:
- WAL mode recovery is automatic on next open
- Backup at `/data/aria_backups/YYYY-MM-DD.json.gz` (R-F224) holds
  Redis-style key snapshots that can be replayed via a reverse
  migration script (not shipped — write one if needed)

**`/data` volume detach**:
- Already a catastrophic failure (knowledge.json + signals.json +
  RAG also live there). The disk-files backup (R-F224) is your only
  recovery path for any of these. Operator should snapshot the volume
  off-host weekly via `fly ssh sftp` if data sovereignty matters.

**Single-machine outage**:
- Same blast radius as today. The migration doesn't change the HA
  story. If multi-instance becomes a requirement later, the abstraction
  in `redis_store` lets us swap back to Upstash by flipping
  `ARIA_STATE_BACKEND=upstash` — no code change.

**SQLite write contention**:
- ARIA's volume (~1-5 writes/sec peak) is well below SQLite's
  thousands-of-writes-per-sec capability in WAL mode. If a future
  feature pushes much higher write rate, profile first and revisit
  the schema.

---

## 8. Open follow-ups

- **chat_audit_log file-mode**: currently uses Redis chain. Move to
  an append-only JSONL on disk so it survives Upstash drop. Same
  HMAC, just disk-backed. (~2 hours of work; deferred until SQLite
  proves stable.)
- **Cooldown state**: currently in `_stats` dict in `fallback.py`
  with Redis mirror. Move the Redis mirror to SQLite via state_store
  (transparent — `fallback.py` calls `redis_store.set` which now
  dispatches to SQLite).
- **mem0 facts**: stored as `mem0:session_*` source-tagged knowledge
  rows. Already disk. No change needed.

---

## 9. Verification per protocol

Per `docs/verification_protocol_2026_05_11.md`, this commit ships
with the standard checklist. Specifically the verification agent
must confirm:
- Every public function in `redis_store` has matching dispatch logic
  for `_use_sqlite()` (A: function call sites)
- `state_store` API surface matches `redis_store` exactly (B: new
  functions match callers' expectations)
- TTL semantics (`ex=None` vs `keepttl=True`) behave identically
  between backends (D: edge-case parity)
- Migration script handles every Redis type ARIA actually uses
  (C: field access — keys all enumerated)
- The R-F236 `ARIA_BRAVE_DISABLED` env check happens BEFORE the
  sticky-disable check (G: env flag ordering)

Verification ran after shipping; report attached to the commit
message.

---

## 10. Posture summary

ARIA is now structurally closer to Claude Code than to a SaaS app:
- Memory: disk files (knowledge.json, signals.json, RAG, mem0)
- State: disk SQLite (sqlite_state.db) — was Redis
- Search: free providers only when `ARIA_BRAVE_DISABLED=1`
- Inference: Anthropic today → ARIA-LLM sovereign Phase 4

The only remaining external paid dependency is the LLM itself, and
that has a documented migration path to sovereign hosting. Robust
independence is no longer aspirational; it's an env-flag flip away.
