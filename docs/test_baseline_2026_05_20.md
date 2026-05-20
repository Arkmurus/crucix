# Test baseline refresh — 2026-05-20 (R-F747)

Audit on 2026-05-20 found the previously-documented pytest baseline
("3333 tests / ~125s / 27 known-fails, 2026-05-18") drifted from
reality. This document records the refreshed cold run + the 11
distinct R-cluster regressions surfaced.

## Numbers

| Metric              | Old (CLAUDE.md 2026-05-18) | New (2026-05-20, R-F747) | Δ        |
|---------------------|----------------------------|--------------------------|----------|
| Tests collected     | 3333                       | **3647**                 | +314     |
| Wall time           | ~125s                      | **166s**                 | +41s     |
| Passing             | 3306                       | **3575**                 | +269     |
| Failing             | 27 known-fails             | **72 (11 R-clusters)**   | +45      |

Command (Windows venv, Python 3.14.3, pytest 9.0.3):

```
.venv\Scripts\python.exe -m pytest aria_service/tests/ -q --no-header --tb=no
```

Exit code 0 (pytest counts but does not enforce; the failing tests
are pre-existing regressions, not collection errors).

## Failing R-clusters (11 distinct, 72 individual tests)

| R-cluster | Module                                        | Hypothesis                                                                                  |
|-----------|-----------------------------------------------|---------------------------------------------------------------------------------------------|
| R-F434    | brandified hostname cap                        | Output-cap rule on brandified hostnames in copy/render path                                 |
| R-F436    | page entity extraction                         | Page-level entity-extraction heuristic regressed                                            |
| R-F445    | polyglot execute                               | Multi-lang execution stub / harness drift                                                   |
| R-F450    | upload magic-byte routing                      | MIME magic-byte dispatch ↔ filename-routing collision — **overlaps task #9 magic-bytes**    |
| R-F460    | brain absorb pause                             | Brain absorb pausing / circuit-breaker timing                                               |
| R-F463    | memory replication patterns                    | RAG / knowledge replication semantics                                                       |
| R-F468    | mistake_ledger no TTL                          | Ledger no-TTL contract regression (R-F239 zone)                                             |
| R-F513    | build_rev autoderive                           | `/health/live` build_rev field not flowing from build args                                  |
| R-F528    | read_document clientdisconnect                 | Upload-cancel handler regressed (R-F725 hard-cap zone)                                      |
| R-F574    | self-improve discard                           | Self-improve staged→discarded transition broken                                             |
| R-F672    | lifespan silent except promoted                | Lifespan exception-handler promotion check                                                  |

Each cluster needs its own triage commit; do not bundle. Pickup-list
entries should be opened against the matching R-Fxxx with the test
file path and a one-line repro.

## Refresh cadence (binding, added to CLAUDE.md §16 in same session)

> Re-run `pytest aria_service/tests/ -q` after every **100 R-numbers
> shipped** (next refresh ~R-F850) **or** any session landing **≥5
> commits to aria_service/** — whichever comes first.
>
> New R-numbers must not add to the failing-test count.

The 2026-05-18 baseline lasted 18 days and 314 new tests without
a refresh. Without an explicit cadence, the baseline went stale by
~10× the failing-test gap before being detected.

## What this does not cover

- Per-test latency profile (slow-fixture identification) — out of scope.
- Coverage report (% lines covered) — pytest didn't run with `--cov`.
- Cause attribution per cluster — each row above is a hypothesis to
  triage, not a confirmed root cause.
- Live fly state — this is the local Windows dev run; CI baseline
  may diverge on Linux/Docker (different Python minor, native
  dependency stack).

## R-F747 deploy footprint

None. This is a documentation commit; no aria_service/ behaviour
changes. Reservations entry advanced from `in_progress` to `shipped`
against this commit's SHA. The corresponding local CLAUDE.md §16
update is in the operator's working copy (CLAUDE.md is gitignored
since 2026-03-15 per commit 710e52f — kept per-machine).
