# 2026-08-11 — tool-use compounding cycle handoff

Resume phrase: **run cycle**.

When the operator says `run cycle`, resume from the mixed-retention design gate
below. Do not regenerate the completed raw-base collection and do not launch the
51-pair file as a pure-DPO cycle.

## Grounded state at handoff

- R-F3829, commit `887a7b018593c03318d033016e98a6fdd7ed0642`, aligned
  the archive-guard capability test with the consolidated persistence path.
- R-F3828, commit `8d530a6ab268426a521b3ed962a3af41cb8641be`, fixed
  continuation DPO to load a trainable policy adapter and an independent frozen
  reference adapter from the same parent checkpoint. The prior continuation
  path compared the child policy with raw base, not its parent.
- R-F3830, commit `878b191a87b34f8438a55e2824f1d0b488dab1e0`, added
  deterministic balanced raw-base collection and explicit base-only serving.
  It preserved adapter-mode validation and arms the pod self-stop watchdog before
  publishing launch state.
- All three commits were pushed to `origin/main` and ship-marked.
- Pre-commit verification for R-F3830: 11 focused capability tests passed;
  strict preflight passed for 90 train / 168 eval rows; train and eval contained
  45 / 50 disjoint entities; zero of 90 train rows overlapped 480 frozen golden
  entities; all 258 rows rendered; longest prompt was 2,711 tokens under the
  4,096-token ceiling; 588 production Python files compiled.
- Queue: `data/training/tooluse_base_balanced_v1_queue.jsonl`.
  SHA-256: `7D2C4103A4B05EE09C7D784B1EA100A70AC316AD9A3AC80FBF7C54BA79421AE5`.
- Completed report:
  `data/eval_reports/aria_tooluse_base_balanced_v1_generations.json`.
  Live parse at handoff: `complete=true`, 90 rows.
- Preference file:
  `data/training/aria_tooluse_base_balanced_v1_dpo.jsonl`.
  It contains 51 pairs from 27 subjects.
- Collection pod `0gfs7zfiyccyaz` was live-probed as `EXITED` at handoff. No
  training pod was launched after pair analysis.

## Measured preference-pair coverage

| Axis | Real raw-base failures / pairs |
| --- | ---: |
| `tooluse_challenge` | 12 |
| `tooluse_challenge_unavailable` | 11 |
| `tooluse_trace_unavailable` | 11 |
| `tooluse_multihop` | 7 |
| `tooluse_person` | 6 |
| `tooluse_trace` | 4 |
| `tooluse_adverse` | 0 |
| `tooluse_contradiction` | 0 |
| `tooluse_resolution` | 0 |
| `tooluse_news` | 0 |

The zero-pair axes are not missing because collection failed. Raw base answered
those sampled rows honestly, so there is no genuine rejected response from which
to construct a DPO pair. Inventing negatives would violate the training contract.

## Why the paid training launch was blocked

The preceding fresh-v2 candidate trained on 44 challenge-only pairs and scored
128/168 against the raw-base incumbent's 136/168. Challenge remained 10/24 while
adverse and multihop regressed. Large training reward margins therefore did not
prove eval improvement.

The new 51-pair set is honest but covers only six of ten axes. Pure DPO would
optimize those failures with no rehearsal signal for the four currently healthy
axes. Launching it would repeat the narrow-objective failure class. The block is a
quality gate, not a GPU-availability or timeout issue.

## Exact continuation plan for `run cycle`

1. Re-probe the three commits, both artifact files, their row counts, queue hash,
   shared-tree ownership, and confirm every RunPod pod is `EXITED`. Treat this
   record as a pointer, not as live status.
2. Map the existing SFT and DPO launch chain before editing. Verify the pinned TRL
   and PEFT APIs again if dependency versions changed.
3. Reserve a new R-number and implement a minimal mixed-retention cycle:
   genuine DPO negatives for the six failing axes plus chosen-only supervised
   rehearsal for adverse, contradiction, resolution, and news. Rehearsal rows
   must come from the train split, remain disjoint from held-out/golden entities,
   and preserve the exact tool-use output contract.
4. Add unit and capability tests proving: deterministic axis quotas; no held-out
   contamination; no synthesized DPO negatives; all ten axes have an explicit
   learning or retention signal; continuation reference remains pinned; both
   paid phases arm self-stop watchdogs; incomplete artifacts fail closed.
5. Run focused tests, strict preflight, diff check, full production compile, and
   adversarial self-review. Commit only owned files and push before GPU spend.
6. Launch one bounded candidate from raw base. Persist SFT and DPO intermediate
   adapters atomically so a stopped pod cannot strand the result.
7. Evaluate the candidate on the unchanged 168-row held-out split. Compare totals
   and every axis against the live raw-base incumbent report. Promotion requires
   an explicit no-regression policy for protected axes and measured improvement
   on targeted axes; do not promote from training loss or reward margin.
8. On any failed gate, stop the pod, retain diagnostics, write a verdict artifact,
   and do not deploy. On success, archive, commit the verdict/manifest changes,
   push, and follow the repository's promotion protocol.

## Shared-tree caution

Claude was working concurrently in the same checkout. At handoff the tree had
unrelated modified application/docs files and `data/claude_distill/`. Never stage
them with training work. Use exact-file staging, inspect `git diff --cached`, and
re-check ownership immediately before every commit.

## General lesson

A balanced prompt queue does not guarantee a balanced DPO dataset: correct model
answers generate no negative preference pair. Retention must therefore be a
first-class training signal and promotion gate, not an assumption inferred from
collection coverage.
