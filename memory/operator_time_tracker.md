# Operator time tracker

**CLAUDE.md §20 close ritual: update this file with session hours + R-numbers
shipped + cumulative pace_ratio.**

## Why this file starts on 2026-07-30

It did not exist before today. `git log --all -- memory/operator_time_tracker.md`
returns nothing and `git rev-list --all --objects` finds zero matching objects —
the path has never been tracked. §20 has named it as a binding session-close step
for months and the step silently never ran, the same failure shape as R-F2623
(the §20 coding-RAG priming snippet raised `TypeError` on every invocation, so
that binding step never ran either). A rule that points at a file nobody created
is not a rule that is being followed.

**Prior sessions are NOT reconstructable and are deliberately not invented here.**
Commit timestamps would give a span, but a span is not operator hours, and
back-filling a plausible history is exactly the fabrication this project exists to
prevent. The series starts empty and accrues honestly from here.

**`pace_ratio` is UNDEFINED until the operator defines it.** §20 requires a
"cumulative pace_ratio" but nothing in the repo states what it divides by
(R-numbers per hour? planned vs actual? operator hours vs agent hours?). Recording
a number under a label whose meaning is unknown would be worse than recording
none. **Operator: state the intended definition and it will be computed from this
table forward.**

## Attribution caveat (2026-07-30)

Two Claude Opus 5 agents worked the same tree today, so the
`Co-Authored-By: Claude Opus 5` trailer does **not** distinguish authors — 58 of
today's 114 commits carry it, including several that were demonstrably the peer's
(R-F3527, R-F3530). R-numbers below are attributed from the session transcript,
not from the trailer. See [[two-agents-one-tree-hazard]] and
[[shared_tree_corruption_two_agents_2026_07_26]].

---

## Sessions

| Date | Span (commit clock) | R-numbers shipped (mine) | Count | Notes |
|---|---|---|---|---|
| 2026-07-30 | 00:02 – 23:42 UTC (repo-wide, both agents) | R-F3457, R-F3464, R-F3469, R-F3475, R-F3476, R-F3477, R-F3479, R-F3483, R-F3485, R-F3486, R-F3487, R-F3491, R-F3494, R-F3495, R-F3497, R-F3499, R-F3500, R-F3503, R-F3505, R-F3506, R-F3509, R-F3511, R-F3513, R-F3515, R-F3517, R-F3518, R-F3519, R-F3521, R-F3523, R-F3525, R-F3526, R-F3528, R-F3529 | 33 | All verified live. Peer shipped R-F3520, R-F3522, R-F3524, R-F3527, R-F3530 in the same tree. |

**Span is repo clock, not operator hours.** The first/last commit timestamps cover
both agents and include unattended build/deploy waits (aria-intel cold boot is
~10 min per deploy). Do not read 23h40m as operator effort.

### 2026-07-30 — what the day actually produced

Grouped by workstream, with the outcome rather than the diff:

- **Live-log DD (15 cycles)** → `docs/LIVE_LOG_DD_2026_07_30_15_CYCLES.md`, 13
  findings. Three of my own findings were WRONG and were corrected in the doc:
  #8 (73% aiosqlite was a census of parked threads, not I/O saturation), #3 (the
  fallback cache was fine; every `complete()` had raised), #9 (the OpenSanctions
  key IS sent — the 429 is a monthly quota).
- **News pipeline** — silent permanent article loss closed on two ingest paths
  (R-F3486); MARKET_HEATING stopped counting syndicated copies as corroboration
  (R-F3487); archive-wide resumable classifier replay (R-F3494); three dark
  failure branches wired (R-F3495); selective enrichment with a confidence cap so
  a headline cannot pass as a read article (R-F3499/R-F3509); claim-level
  absorption that must be verbatim-quotable or refused (R-F3511).
- **Watchlist** — a store reconnect could wipe **every tenant's** watchlist
  (R-F3506); the UI reported success when nothing was removed (R-F3503); deleted
  DDs kept being monitored and logged by name (R-F3500); orphan reconcile, dry-run
  by default (R-F3505 — my first matcher read fields the index does not carry and
  would have deleted all 8 entries).
- **Truthful news UI** — the list and the category breakdown queried different
  populations, so a category read "No articles" beside a bar saying it had dozens
  (R-F3517 backend, R-F3518 web, R-F3519 dash-guard repair).
- **Temporal compounding** — the correlator treated time as a binary filter
  (R-F3521), the trajectory reached no surface (R-F3523), the two bands used
  different story identities (R-F3525), and ACCELERATING turned out to be
  measuring ARIA's own ingestion growth — 53 of 54 countries — until measured
  relative to the corpus (R-F3526). Live: 47 SUSTAINED / 3 ACCELERATING / 4
  DECAYING.
- **OpenSanctions** — the monthly plan quota is spent; a 429 now separates
  quota-exhausted (operator-only) from a per-second limit (R-F3528), and the DD
  screen gained a floor: local canonical OFAC/EU lists (24,953 rows live) when the
  paid aggregator cannot answer (R-F3529). Proven live on the real DD path with
  the quota still spent: `Rosoboronexport → screened=True, blocked=True,
  kind=local_canonical`.
- **Operator levers** — LLM cooldown clear after a billing top-up (R-F3513); the
  §17 doc claim that a restart clears a billing cooldown was false and is
  corrected.

### Standing operator items from this session

- **OpenSanctions plan quota is spent.** Screening degrades to local OFAC/EU
  instead of going dark, but the ~200-list breadth is unavailable until the plan
  is upgraded or the month rolls. Only the operator can clear it.
  Status: `GET /api/aria/sanctions/source/status`.
- **`pace_ratio` definition** — see above.
- **DD screen endpoints** (`/explore-deep`, `/sanctions/rca`,
  `/sanctions/divergence`) were 502ing; that was the peer's SIGSEGV crash loop,
  not endpoint latency as I first claimed. Healthy after their R-F3530: 0.2–1.3s.
