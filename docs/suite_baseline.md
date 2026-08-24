# Suite baseline — THE authoritative record

> **This file supersedes `suite_baseline_2026_07_28.md` and
> `suite_baseline_2026_07_30.md`.** Both are retained only as the history section at
> the bottom. There is ONE baseline; do not add a fourth file.

## Current baseline — 2026-08-24, tool-recorded, provably clean

```
VALID=YES
81 failed, 17,094 passed  (17,175 collected across 2,057 files)
sha a07a6760   tree ac0d0b9a3814724f (identical before AND after)
recorded by:  python scripts/admin/suite_baseline.py --single-process --record
measured on:  the main checkout, quiet (peer idle; tree hash proves it)
```

**Set diff against 2026-08-17 (113 @ `bf680ed1`, 1,923 files): 76 standing ·
37 FIXED · 5 new.** The count fell by 32 while 1,268 tests were ADDED, so read the
set, never the count.

**Why the re-record mattered, and it was the dangerous direction.** The 2026-08-17
entry had gone stale in the way that costs you a P0: five tests fixed in the
2026-08-23/24 sessions were still listed as known-failing. A fixed test carried as
"known" means a future re-break reads as expected and nobody looks.

### The 5 new entries, all attributed — three were fixed on discovery

| node id | verdict |
|---|---|
| `test_rf3720_secret_scan_gate::test_the_repo_itself_is_clean` | **FIXED — R-F4297 / C-251.** Not a finding: the scan TIMED OUT at 90s. It was reading 15.6 GB (nine 335 MB LoRA safetensors, training corpora) because the binary check ran one line AFTER `read_text()` of the whole file. 5m45s → 6.4s. |
| `test_rf4117_secret_baseline_is_not_a_hole::test_the_repo_passes_its_own_secret_gate` | **FIXED — same root cause, same commit.** |
| `test_rf4205_no_tolerated_xfails::test_verifier_main_blocks_a_tolerated_xfail` | **FIXED — R-F4298 / C-252.** `relative_to(_REPO)` raised on pytest's tmp_path, so the gate crashed instead of reporting. It had NEVER passed under a default tmpdir — it went green only under `--basetemp` inside the repo. New here because R-F4205 postdates `bf680ed1`. |
| `test_rf4273_read_timeout_burst_must_be_active::test_a_boot_window_burst_is_labelled_not_merged` | **ORDER-DEPENDENT, not a regression.** Passes standalone. Same class as the §16 known-flaky set; polluter not identified. |
| `test_rf2200_neural_index_offload::test_rf2200_incremental_rebuild_keeps_loop_responsive` | **KNOWN §16 FLAKE.** Asserts event-loop latency against a hard threshold; passes in isolation. Already documented below. |

> **The JSON still lists the three fixed entries, deliberately.** It is a
> MEASUREMENT taken at `a07a6760`, and hand-editing it to reflect later commits
> would falsify the record rather than correct it — the same reasoning that got
> R-F4282 abandoned. The next `--record` run will drop them. Until then, this
> table is the correction.

## Current baseline — 2026-08-17, tool-recorded, provably clean, environment-stamped

```
VALID=YES
113 failed, 15,794 passed  (15,907 collected across 1,923 files)
sha bf680ed1   tree d14505b6ed5e633d (identical before AND after)
env  python 3.13.14 · win32/ARM64 · 123 packages · 506de2c206be9c7e · fastapi 0.141.1
recorded by:  python scripts/admin/suite_baseline.py --single-process --record
measured on:  a git worktree at bf680ed1 (see "quiet tree" below)
```

**Set diff against 2026-08-11 (90 @ `168674b2`, 1,738 files): 71 standing · 19 fixed
· 42 new.**

**The 42 "new" are overwhelmingly NEW TESTS, not regressions.** 185 test files were
added between the two recordings (1,738 → 1,923). A test that did not exist when the
baseline was taken cannot be in it, so every failing new test reads as "new". Most are
the training/DPO suites. **Zero of the 42 are in the work landed by this session**
(R-F4083..R-F4117) — checked by name against the failure set, not assumed.

Two entries were run in isolation before publishing this number, because both would
otherwise be read as something they are not:

* `test_lifespan_smoke::test_lifespan_starts_and_shuts_down_cleanly` — §9 treats a
  lifespan failure as the boot-outage class, so this one matters. It is **not** a boot
  defect: it fails on `PermissionError: [WinError 32]` cleaning up a subprocess's
  `err.txt`, a win32 tempfile-teardown artifact. Direct `lifespan(app)` invocations
  returned `LIFESPAN OK` repeatedly the same day.
* `test_rf1664_1665_wedge_cure::…records_latency_before_neural_runs` — **passes in
  isolation**, so order-dependent, not contention. Worth stating because the box was
  running at ~49% under a concurrent Codex training driver during this measurement, and
  the two latency-threshold flakes §16 names (`rf2144`, `rf2200`) are **absent** from
  the failure set — so contention did not manufacture entries here.

⚠️ **A KNOWN POLLUTER IS LIVE IN THIS MEASUREMENT, deliberately.** The peer agent's
C-158 fix — `brain_hook._stats_flush_lock` is a module-level `asyncio.Lock`, so one
`asyncio.run()` closing mid-acquire latches stats coalescing off for the life of the
process — was **verified but unpushed** when this ran. §16 lists that brain-hook family
as the dominant known-flaky set with the polluter *unidentified*; it is identified now.
**When that fix lands, several entries here will go GREEN. That is a FIX, not drift** —
do not read the next set-diff as a regression in the other direction.

**Recording hazard hit and repaired during this run (worth knowing).** A reflex
fetch-then-fast-forward mid-measurement pulled two new test files into
`aria_service/`, changing the hashed set and heading for `VALID=NO`. Restoring the
worktree to the measured commit brought the tree hash back to
`d14505b6ed5e633d` — verified equal to the run's opening hash, with
`dirty_measured_files() == []` — so the run stayed valid. **Do not fetch into a
worktree that a baseline is reading.**

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
