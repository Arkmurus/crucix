# SCOPE — Golden Intel corroboration engine (R-F2633)

**Status: SCOPE ONLY. No code written. Awaiting operator go + a §1 phase call.**
Author: Claude · 2026-07-15 · Follows CLAUDE.md §1/§6/§8/§21a/§22, AGENTS.md §8.7.

---

## 1. The finding (evidence, not inference)

`Distribution Ready = 0` on the dashboard is **structural, not a bug in the poll**.
R-F2630 fixed the poll's frozen tail — the column stayed 0 anyway, which **disproves**
the poll-staleness explanation empirically (poll went `fresh`, `reasons=[]`, DR still 0).

**Corroboration is impossible today:**

| fact | evidence |
|---|---|
| `evidence_count >= 2` is what earns `corroborated` | `news_monitor.py:665`, `:638`, `:713` |
| Those 3 sites are **readers only** | grep: no writer produces `>= 2` |
| The RSS path defaults it to 1 | `news_monitor.py:691` — `article.get("evidence_count") or ... or 1` |
| The bridge adapters **hardcode** it to 1 | `golden_intel_bridge.py:559, :731, :790` |
| Live: every signal is 1 | `evidence_count: {1: 20}`, `corroboration: {'single-source': 20}` |

**Consequence:** the Mining Queue is a **roach motel**. Its only exits are corroboration
or a tier upgrade; corroboration can never happen. The dashboard's *"8 candidates
awaiting corroboration"* is a promise the system cannot keep.

**`evidence_count = 1` is itself a false claim** — the system asserts "single-source"
as a *measured* fact while never measuring. Same class as R-F2621 (GREEN default
emitted as a verdict) and R-F2622 (gate #3 certified on an empty ledger).

## 2. Why this is the highest-USP work

ARIA's USP is **honest, never-false-clean intelligence**. Today the pipeline is
**honest but INERT**: it correctly refuses to publish uncorroborated news (USP working)
but can never earn the right to publish. *A system that always says "insufficient
evidence" is trivially never-false-clean and delivers zero customer value.*

Corroboration is the USP made **productive** rather than merely defensive.

**Explicitly NOT the fix: wiring RSS straight to `distribution_ready`.** Those are
single-source headlines (*"US says it launched new wave of strikes against Iran"*).
Publishing them as decision-grade would be the exact fabrication ARIA exists to
prevent — trading the USP for a vanity metric. The Mining Queue is the honest place
for them. **Leave that gap alone.**

## 3. The prize — proven on live data

Clustering the 20 live signals by entity:

```
entity=('Iran',) -> 10 signals from 3 DISTINCT sources
  [76] US DoD Daily Contracts (tier_1b)  "Centcom Completes Another Wave of Strikes Against Iran"   <-- OFFICIAL PRIMARY
  [70] Middle East Eye                   "US says it launched new wave of strikes against Iran"
  [65] Al Jazeera                        "US attacks Iran as IRGC claims strikes on US military sites"
```

An **official DoD primary source corroborating two outlets on the same event** is
textbook decision-grade Golden Intel. It is sitting in the Mining Queue, undistributable.

## 4. The trap — why a naive version is WORSE than today

The same live data shows both failure modes:

1. **Duplicates are not corroboration.** *"US says it launched new wave of strikes
   against Iran"* appears **3× from Middle East Eye**. Counting them ⇒ `evidence_count=3`
   from ONE source ⇒ **false corroboration**.
2. **Entity-only clustering false-merges distinct events.** `('Iran',)` groups
   *"US strikes Iran"* with *"US resumes Iran ports blockade"* — different stories.

A naive implementation would report `evidence_count=10` and publish confident lies.
**That converts an honest "no" into a false "yes" — strictly worse than the current
inert state, and a direct USP kill.** This risk, not the plumbing, is what makes the
design non-trivial.

## 5. The design — WIRE what exists, don't build

**★ The hard part already exists.** `verified_intel.py:747 SourceIndependenceChecker`:

> *"Reuters and AFP often cover the same press conference. Al Arabiya and Al Jazeera
> both report the same presidency statement. Sources in the same family citing each
> other are not independent."*

- `are_independent(a, b) -> bool` (`:756`) — same `independence_group` ⇒ NOT independent
- **`get_independent_count(sources) -> int` (`:781`)** — literally the `evidence_count` producer
- `SourceRecord` (`:277`): `url, tier, score, domain, retrieved_at, excerpt, title,
  language, independence_group`

**Every input already exists** (verified live): 20/20 signals carry structured
`entities` (countries/products/oems), 20/20 carry `published`, and the 76-feed config
tuple `(name, url, category, lang, tier, topics)` supplies `tier` + url→domain.

**§6 compliant: no new third-party, no embeddings.** Deterministic clustering only —
which also avoids the R-F703 GIL/import-lock stall class (the 44.5s freeze was torch
being imported for embeddings).

### Pipeline (new module: `intel/corroboration.py`)

```
articles/signals
   -> 1. CLUSTER   (entity-set ∩ time-window ∩ title-similarity)   <-- the only NEW logic
   -> 2. SourceRecord per member (url -> domain -> independence_group, tier)
   -> 3. evidence_count = SourceIndependenceChecker().get_independent_count(records)
   -> 4. existing readers (news_monitor:665/638/713) label corroborated + quality
   -> 5. bridge re-assesses customer value -> HONEST distribution_ready
```

**Clustering rule (fail-closed):** members must share ≥1 entity **AND** fall in the same
time window (~48h) **AND** exceed a title-similarity floor (token Jaccard — deterministic,
no model). Anything uncertain **stays single-source**. Never merge on entity alone.

**Independence rule:** derive `independence_group` from the registered feed (domain
family). Same group ⇒ 1 vote, no matter how many articles. This is what kills the
"3× Middle East Eye" false corroboration.

## 6. Tests (§3c — capability, drives the real path)

Every one is a live-data regression:

1. `3x same-source duplicates -> evidence_count == 1` (the MEE case) — **the anti-trap test**
2. `DoD + MEE + AlJazeera on one event -> evidence_count >= 2 -> corroborated`
3. `"US strikes Iran" vs "Iran ports blockade" -> NOT merged` (different events, same entity)
4. Non-regression: a genuinely single-source signal stays `single-source`, DR unchanged
5. Fail-closed: unparseable/missing entities or timestamps ⇒ single-source, never merged
6. §21a: cluster/merge outcomes reach the brain on success AND failure

## 7. Risks

| risk | mitigation |
|---|---|
| **False corroboration** (worse than inert) | fail-closed clustering; independence-group vote; tests 1+3 are the gate |
| Wire syndication (Reuters via 5 outlets) | `SourceIndependenceChecker` already models wire services |
| CPU cost on 856 articles/poll | deterministic only; no embeddings; runs in the poll tail, inside R-F2630's reserve |
| Corroboration starved by dead feeds | **42/76 feeds are dead** — see §8 |

## 8. Dependency: this needs the dead feeds fixed

Corroboration requires **multiple independent LIVE sources covering the same event**.
With **42/76 feeds failing (55%)**, two independent feeds rarely cover one story.
**The engine (this scope) and the fuel (feed triage) only work together.**
R-F2630 makes the failures visible/nameable for the first time — do the triage on that data.

## 9. §1 PHASE CALL — operator decision required

Phase A gates #2, #3 (re-opened by R-F2622), #5, #7 are **OPEN**.

- **Argument for Phase A:** `evidence_count=1` is a *fabricated measurement*. Making it
  honest is honesty-foundation work, the same class as R-F2621/R-F2622 — not a new feature.
- **Argument for Phase B+:** it is a new capability that unlocks a distribution/product surface.

**I do not consider this mine to decide.** If Phase B+, it needs the explicit override:
*"I understand Phase A gate #X is open. Override anyway."*

## 10. Effort

- `intel/corroboration.py` (cluster + wire the existing checker): ~150-200 lines
- Wire into the poll tail + bridge re-assess: ~20 lines
- Tests: 6 capability tests
- **Deliberately out of scope:** feed triage (§8), poll concurrency (deferred from R-F2630),
  the false-fresh overwrite, the misleading dashboard copy.
