# Suite baseline — THE authoritative record

> **This file supersedes `suite_baseline_2026_07_28.md` and
> `suite_baseline_2026_07_30.md`.** Both are retained only as the history section at
> the bottom. There is ONE baseline; do not add a fourth file.

## Current baseline — 2026-08-09, tool-recorded, provably clean, environment-stamped

```
VALID=YES
89 failed, 14,610 passed   (14,699 collected across 1,731 files)
sha e68f0088   tree 8acea472e2979109 (identical before AND after)
env  python 3.13.14 · 121 packages · 0871fe4d97709643 · fastapi 0.141.1
recorded by:  python scripts/admin/suite_baseline.py --single-process --record
measured on:  a git worktree at e68f0088 (see "quiet tree" below)
```

**Set diff against 2026-08-01 (103 @ `cd522878`): 87 standing · 16 fixed · 2 new.**
Both new entries pass standalone, so they are order-dependent rather than deterministic
regressions; the cause is not established and is recorded as open in CLAUDE.md §16.

**R-F3794 — this is the first baseline carrying an ENVIRONMENT fingerprint.** A failure
set is a function of the code AND the installed packages, and only the first was ever
recorded. The 2026-08-08 diff read as "36 new failures" when at least five were a
FastAPI behaviour change and two were an id-parsing artefact. The compare path now
prints a warning when the environment differs, or when a baseline predates
fingerprinting at all.

**R-F3818 — measure on a QUIET WORKTREE.** Two attempts in the main checkout failed,
for two different reasons, and the tool caught both rather than a human noticing:

| attempt | result | why |
|---|---|---|
| 1 | `VALID=YES`, **refused to record** | two tracked files had uncommitted changes, so the `commit` label would have named a sha that did not contain what ran |
| 2 | `VALID=NO` | the peer agent rewrote those same files mid-run |
| 3 | `VALID=YES`, recorded | run in `git worktree add /c/aria_m e68f0088`, which the peer cannot reach |

Confirm the worktree is equivalent before trusting it — a data-dependent subset gave
an identical 1-failed / 118-passed in both checkouts. Copy the recorded JSON back into
the main checkout to commit it.

**Command** (single process, no env overrides, network guard ON):

```
python -m pytest aria_service/tests/ -q --tb=line -p no:cacheprovider --timeout=600
```

**R-F3622 — how to measure it, and why the old instruction did not work.**
This section used to read *"Measured by `scratchpad/measure.py`, which snapshots a
SHA-256 over every tracked `aria_service/**/*.py` before and after the run and prints
`VALID=YES|NO`."* **That file never existed in the repo.** It was written into a
session scratchpad and went with the session, so the one number this repo treats as
authoritative could not be reproduced by anyone — and the check that made it
trustworthy was not part of the committed tool that records baselines.

The validity check now lives in that tool, `scripts/admin/suite_baseline.py`:

```
python scripts/admin/suite_baseline.py            # run + diff the FAILURE SET, prints VALID=YES|NO
python scripts/admin/suite_baseline.py --record   # re-record docs/suite_baseline.json
```

It hashes every tracked `aria_service/**/*.py` before and after the run, prints
`VALID=YES|NO`, and **refuses to `--record` when the tree moved** — a baseline measured
while the code under test changed is not a baseline. `VALID=NO` means DISCARD, not
publish.

**R-F3625 — two modes, and only one of them is the §16 number:**

- **`--single-process`** runs the whole suite in ONE pytest process. Order-dependent
  failures are VISIBLE. **This is the §16 figure** and the 103 above was produced by it.
- **default (segmented)** runs foreground chunks. Each chunk gets a fresh interpreter,
  so state leaked from test A into test B can never bite — it measures a **FLOOR**
  (the repo's record is 149 segmented vs 165 single-process). Faster and survives an
  external kill, but never quote it as the §16 number.

The recorded JSON stamps which mode produced it (`method`) so the two cannot be
confused after the fact. R-F3624: an invalid run exits **3** and emits no verdict at
all — "the measurement failed" must never look like "the code failed" (exit 1).

### Why the validity record exists — read this before quoting any number

R-F3597. `inspect.getsource(func)` takes the line range from the **imported** code
object and slices the file **from disk at call time**. On a tree two agents share, a
commit landing mid-run shifts those lines and getsource returns **a different
function's body** — silently, because the wrong slice is still valid Python. The only
symptom is an assertion that quietly stops matching.

Measured on Python 3.14.3: after 7 lines were inserted above a function,
`getsource` returned `'# c
# c
...

def target_function():
    """'` — a
misaligned block starting at the stale offset.

**This corrupted the first two attempts at this baseline:**

| run | result | verdict |
|---|---|---|
| 1 | 147 failed / 1:16:56 | **DISCARD** — 4 peer commits mid-run touched `routes/aria.py` + `aria_engine.py` |
| 2 | 110 failed / 50:52 | **DISCARD** — 5 commits mid-run, one of them mine |
| 3 | *(completed)* | **LOST** — the harness itself destroyed the output (`capture_output` returned None) |
| 4 | **112 failed / 27:59** | **VALID=YES** |

🔑 **`VALID=NO` means discard the number, not publish it.** The corruption is silent,
so an invalid run looks exactly like a valid one.

⚠️ **Neither historical figure below can be shown to be clean.** They were measured
before this mechanism existed. They are not necessarily wrong; they are unprovable.

### The 147 was NOT a regression

Run 1's extra ~37 failures were the artefact, not defects. Proof by failure-set diff:

```
rf409 (inspect.getsource on routes/aria.py):  run 1 = 8 failures   run 2 = 0
rf730/732/733/734 (getsource on aria_engine): run 2 = 0
```

Run 1's peer commits touched exactly those two files; run 2's touched WA/binding UI,
so the same tests passed. **The real baseline was ~110 throughout.**

Corollary worth stating: the R-F3597 source-probe conversions removed **zero**
failures from run 4 versus run 3, because those tests were not failing in a clean run
to begin with. The fix prevents the artefact recurring; it does not lower a clean
count. An earlier prediction of ~95 was wrong for exactly this reason.

## Two failures that are flaky, not fixed

Both **pass in isolation** (8/8) and failed in run 4 only:

```
test_rf2144_chunked_knowledge_load.py  — chunked sidecar read starved the loop 345ms
test_rf2200_neural_index_offload.py    — event loop stalled 258ms (must be <250ms)
```

These assert **event-loop latency against a hard threshold** and missed by 8ms and
95ms. NOT diagnosed. Note a faster machine would make them pass, so "the box was
idle" does not explain them — do not assume that reading. Judge the suite by the
failure SET, never the count, precisely because of entries like these.

## The failing set — see `docs/suite_baseline.json`

The per-file list that used to live here has been REMOVED, not lost. It is written by
`--record` into `docs/suite_baseline.json` (`failures`, 103 node ids), which is also
what the §16 gate diffs against. Maintaining the same set in a hand-edited doc AND a
tool-written file guarantees they drift, and then nobody knows which is the baseline —
the exact two-sources failure this file was created to end.

```
python -c "import json;print(*json.load(open('docs/suite_baseline.json'))['failures'],sep=chr(10))"
```


## Triage method (reusable)

Run every failing file ALONE and diff against the full-run set. A test that passes
alone is contaminated; one that fails alone is a genuine defect. Never run the
suspects together — that can reproduce the contamination you are trying to measure.

Applied to run 1: **72 order-dependent / 75 genuine**. Half the order-dependent ones
turned out to be the getsource artefact above, not leaked state.

**Known genuine leak, three-line reproducer (open):**

```python
def test_zzz(): from aria_service import aria_engine   # ANY prior test
# then rf3489 :: test_the_other_user_sees_only_their_own
# E  AssertionError: assert ('CIF' in '')   <- mem0 recall returns EMPTY
```

A bare import is sufficient. `rf3489` passes 12/12 alone.

## Refresh rule

Re-measure after every 100 R-numbers shipped, or any session landing >=5 commits to
`aria_service/`. **Ask for a quiet tree first** — a run on a tree another agent is
editing produces a number that cannot be defended. Update THIS file; do not create
another.

## History (superseded, retained for the audit trail)

- **2026-07-30 — 111 failed / 12,556 passed / 28:36** at `a0ee0b99` (R-F3448).
  First single-process run, so the first not to be a floor. R-F3449 closed 15
  order-dependent failures across five mechanisms, three fixed in `conftest.py`
  because the victim was never the culprit. No validity record.
- **2026-07-28 — 94 failed / 11,673 passed** at `31782564` (R-F3368). Built from 13
  FOREGROUND segments because background pytest was being killed, so it is a FLOOR —
  blind to order-dependent failures. No validity record.
- The line it replaced claimed *3,647 tests / 72 failing* and had been ~3x understated
  for two months.
