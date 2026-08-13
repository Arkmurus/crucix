# 2026-08-13 CITATION-PHOENIX outcome

- The rejected v10 calibration child was not rerun, promoted, or used as a
  parent. The accepted v5 adapter remained the only serving parent.
- R-F3948 made the generation-only runner prove watchdog PID liveness and prove
  terminal RunPod state after cleanup. R-F3950 prevents stale local logs from
  being reported as fresh diagnostics after a failed transfer.
- R-F3949 collected 100/100 complete generations over committed train-only
  rows. Strict preflight proved 55 train and 50 eval entities disjoint, zero
  overlap with 480 golden entities, all 268 train/eval renders valid, and a
  maximum length of 2,711 tokens.
- The raw scorer reported 95/100. C-42/R-F3951 replayed the five failures and
  proved four were evaluator false positives: an ordinary sanctions-list
  verdict, two epistemically negated accusation refusals, and one explicit
  identity denial. The corrected current-validator score is 99/100.
- The sole genuine failure is `tooluse_person` for Bashar al-Assad: the model
  asserted identity from a name match although the payload supplied no DOB,
  nationality, or document identifier. The contamination-guarded builder emits
  exactly one preference pair.
- No adverse, news-impact, or citation-source failure occurred in this n=100
  train-only sample. It therefore does not reproduce the rejected v10 citation
  target. One genuine pair is insufficient evidence for another paid DPO cycle,
  so no training candidate was created and no model was promoted.
- Pods `ytfleblhtipw0k` and `55ruz34nfy85eq` were observed `EXITED`. The first
  run terminated without a sentinel and had no trustworthy diagnostics; the
  second completed the 100-row report. At the provider-reported $0.44/hour,
  first-run cost was estimated near $0.25 from timestamps, not from billing.

Artifacts:

- `data/eval_reports/aria_tooluse_citation_phoenix_generations.json`
- `data/eval_reports/aria_tooluse_citation_phoenix_generations_rescored.json`
- `data/training/aria_tooluse_citation_phoenix_dpo_rescored.jsonl`

Do not use `data/training/aria_tooluse_citation_phoenix_dpo.jsonl`: it contains
four pairs derived from pre-C-42 false-positive labels and is quarantined.

Next evidence step: broaden deterministic train-only sampling on citation-rich
adverse/news-impact/person rows without changing calibration or held-out inputs.
Do not spend on preference training unless genuine current-validator failures
produce a sufficiently diverse intervention set.
