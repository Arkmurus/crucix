# 2026-08-12 citation-contract v10 crash recovery

- R-F3938 commit `6f63ec46` rejects a cycle deadline below its declared
  workload envelope before pod creation and reports artifact recovery results
  truthfully. The affected suite passed 64 tests; the pre-push gate passed 228
  tests with 2 expected failures.
- R-F3939 commit `101528ec` was added after a live SSH reset proved that an
  `ARMED` response and a fire-and-forget stop request were not evidence.
  Watchdog arming now reads back the remote PID and proves it alive; cleanup
  verifies the RunPod state is terminal. The live replacement run proved both
  branches.
- Strict preflight covered 594 train and 168 eval rows: 65/50 disjoint
  entities, zero overlap with 480 golden entities, all 762 renders valid, and
  maximum length 2,795 tokens.
- The recovered v10 calibration child was rejected at 24/30 against the 30/30
  parent. Regressions: adverse 3->2, news-impact 3->0, person 3->1. Held-out
  evaluation did not run. The child is prohibited from promotion.
- Diagnostics were read back to
  `data/eval_reports/aria_tooluse_citation_contract_v10_recovery_diagnostics.tgz`.
  Pods `855odubhll6rit` and `zavaoj1ldxuz9c` were both live-probed as
  `EXITED`.

Lesson: a safety control is not armed because the command returned a token, and
a paid resource is not stopped because a stop request was sent. Both claims
require state read-back. A recovered adapter that fails calibration is evidence,
not a continuation parent.

## Closed continuation boundary

**Resume trigger: `CITATION-PHOENIX`**

When the operator sends that exact phrase, continue from this boundary:

1. Do not rerun or promote the rejected v10 calibration child.
2. Treat the 24/30 diagnostic as the live learning target: unsupported citation
   identifiers on adverse/news-impact/person, especially `brain_hook:web_search`,
   invented dates, and URLs absent from tool payloads.
3. Build the next intervention from genuine train-split model failures, preserve
   the unchanged calibration and n=168 held-out gates, and run strict
   contamination/cost/push checks before any GPU spend.
4. Re-probe RunPod state and current repository HEAD at resume time; this record
   is a pointer, not a live-status claim.

Session closed after commit `5920ce8b`, with both observed pods verified
`EXITED` and no candidate promoted.
