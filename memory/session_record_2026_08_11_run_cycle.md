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

## 2026-08-11 continuation — person-first curve v5

### Ordering and structural corrections

- Person screening was promoted ahead of multihop because the failing multihop
  chain terminates in person-screen interpretation. Commit `f98c0620` added the
  person axis to target-tier collection and produced a ten-subject train-only
  delta. The live raw-base collection pod `8b1h073aw0onqp` completed 10/10 and
  was stopped; six outputs were genuine person-screen failures.
- Commit `e5376ab2` merged and current-validator-rescored the 100-row baseline,
  preserved the prior 47 mixed preferences, added six novel person preferences,
  and built curve-v5 assets. Strict preflight passed for 230 train / 168 eval
  rows, 55 / 50 disjoint entities, zero overlap with 480 golden entities, and a
  2,711-token maximum. The affected suite passed 46 tests; 590 production Python
  modules compiled.
- Curve v5 contains 230 positive SFT rows. Person, challenge-unavailable, and
  trace-unavailable are weighted 4x; multihop is weighted 2x; all other axes
  retain 1x coverage. The mixed DPO set contains 53 genuine pairs.
- The first launch exposed a stale remote constant: the host verified 53 pairs,
  but `pod_tooluse_curve.sh` required 47. No training ran. R-F3875 commit
  `93ec21c1` parameterized the remote gate from the host-verified value; 22 tests
  passed and the R-number was ship-marked.

### Measured SFT result

- Fresh-base SFT on the 230-row curriculum completed 28/28 steps with train loss
  1.3391. Pod `xuzogno4yh5iig` stopped after the calibration gate.
- The original scorer reported 20/30 -> 28/30, with person 0/3 -> 3/3 and both
  unavailable axes 0/3 -> 3/3, but marked QinetiQ and Serco multihop answers as
  hits because they said `None ... are sanctioned`.
- This was an evaluator defect, not a model regression: the same validator
  already accepted `not sanctioned`, but its clause-negation grammar omitted
  `none`. R-F3877 commit `0c33fefe` added quantified negation and exact QinetiQ /
  Serco capability tests. The relevant suite passed 51 tests and the R-number
  was ship-marked.
- The retained answers then rescored 30/30 across all ten axes. Reproducible
  baseline: `data/eval_reports/aria_tooluse_curve_v5_sft_rescored.json`.
- Valid positive parent: `data/training/checkpoints/aria_tooluse_curve_sft_v5.tgz`
  (310,493,395 bytes; archive contains exactly one adapter). This is calibration
  evidence only; it has NOT yet been measured on the unchanged 168-row held-out
  split and must not be called promoted.

### Guarded DPO continuation result

- A mixed-retention continuation was launched from the positive v5 SFT adapter,
  never from raw base. Pod `youv04t3hhxo5v` trained 53 genuine pairs for 27/27
  steps. Late-epoch training accuracy was 1.0 and reward margin 10.8768 on the
  53-pair training sample; these are not promotion evidence.
- The mandatory calibration gate rejected DPO at 30/30 -> 28/30. QinetiQ named
  only James Field and Serco named only Amanda Miller; neither answer named the
  company or completed the multihop chain. Held-out evaluation did not start.
- Diagnostics: `data/eval_reports/aria_tooluse_curve_v5_dpo_diagnostics.tgz`.
  The pod is EXITED. `aria_tooluse_curve_dpo_v5.tgz.partial` is prohibited as a
  parent and must remain quarantined.

### Binding next order

1. Treat `aria_tooluse_curve_sft_v5.tgz` as the sole eligible training parent,
   but not as promoted until it passes the unchanged 168-row held-out evaluation.
2. Add a dedicated SFT-adapter held-out evaluation path so SFT can be measured
   before any preference stage. Promotion claims must cite that complete n=168
   report, not the trained-on n=30 calibration.
3. Stop DPO experimentation for this failure class. Two guarded mixed-retention
   attempts learned the preference pairs while degrading multihop transfer.
4. Next learning work is positive multihop SFT diversity: company resolution ->
   officers/controllers -> every person screen -> company-named synthesis. Include
   multiple clean officers and require the final answer to name the company and
   summarize the whole chain. Preserve person and unavailable-axis rehearsal.
5. Any next candidate must first preserve the 30/30 calibration ceiling and then
   improve or preserve every axis on n=168. A regression is rejection, regardless
   of training loss or reward margin.

### Positive continuation results after handoff

- The immutable v5 adapter scored 155/168 on the unchanged held-out set versus
  the current-validator incumbent at 137/168. It regressed adverse 26->24 and
  contradiction 27->26, so it is not promotion eligible. The explicit ledger
  records 10/10 evidence coverage, 6/10 mastered axes, and priority order:
  adverse, contradiction, challenge, multihop.
- Positive-only v6 continued v5 on 120 balanced rows. Its mandatory calibration
  gate rejected 30/30 -> 26/30 before held-out evaluation. Citation grammar and
  multihop subject retention failed. The v6 partial adapter is prohibited.
- R-F3898 commit `3e3c08ee` added a structural full-replay curriculum: all 230
  accepted-parent rows followed by the 120-row delta, 350 positive rows across
  all ten axes, zero DPO. Strict preflight verified 350/168 rows, 65/50 disjoint
  entities, zero overlap with 480 golden entities, all 518 renders, max 2,711
  tokens. The affected verifier passed 228 tests with 2 expected failures.
- Pod `maamoxx5npglea` ran the v7 full-replay continuation and is EXITED. The
  gate again rejected 30/30 -> 26/30: contradiction 3->1 and news-impact 3->1;
  held-out evaluation did not start. Diagnostics are
  `data/eval_reports/aria_tooluse_positive_replay_v7_diagnostics.tgz`; the v7
  partial adapter is prohibited.
- Binding conclusion: do not run another narrow continuation SFT or DPO cycle.
  v5 remains the sole eligible parent. The next work must structurally separate
  tool-payload metadata from citeable source identifiers and teach contradiction
  synthesis that says "no sanctions matches" without asserting CLEAN when
  adverse evidence exists. Prove those contracts on calibration before GPU spend.
