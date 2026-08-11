# 2026-08-11 — R-F3843 mixed-retention tool-use cycle result

The bounded cycle completed and was rejected by the retention gate. No model was
promoted or deployed.

## Live evidence

- Implementation commit: `6e3b44d529ead44be9e3d2e61a44b2e7ece2ce47`, pushed before GPU spend.
- Pod: `r370s566lv0krt`; final RunPod probe reported `EXITED`.
- Candidate report: `data/eval_reports/aria_tooluse_mixed_v1_eval.json`,
  `complete=true`, 168 rows, SHA-256
  `063d0733669b443dba8da6c78447d189a5f6641cb974704407c4e061160cef4c`.
- Incumbent report: `data/eval_reports/aria_tooluse_incumbent_base_current.json`,
  `complete=true`, 168 rows, SHA-256
  `f5e82dab16fefc49178f4254d67f24480e5a1f0af27bc2c3a5e180e2ab97681b`.
- SFT intermediate: `data/training/checkpoints/aria_tooluse_mixed_sft_v1.tgz`,
  SHA-256 `9eaa99d3b9fba384361774226863c17037b099456c70aa87df7af4ba0f128193`.
- DPO adapter: `data/training/checkpoints/aria_tooluse_mixed_v1.tgz`, SHA-256
  `dc6a9199bbf7b6b1bb208857636598f2cd5e6c179a0049cbdfdeda8671d9362e`.
- Both archives contain exactly one `adapter_config.json` path.

## Gate result

Raw-base incumbent scored 136/168; mixed candidate scored 129/168, delta -7.
Adverse regressed 26/28 to 25/28 and challenge regressed 16/24 to 8/24.
Trace improved 33/39 to 35/39; every other axis tied. The authoritative verdict
is `data/eval_reports/tooluse_mixed_v1_verdict.json`: `promote=false`,
`reason=retention_gate_failed`. Training reward was not used as promotion
evidence.

## Lesson

Chosen-only rehearsal on six examples per protected axis did not prevent
negative transfer into challenge or adverse. A mixed objective is necessary but
not sufficient: retention examples need measured weighting/oversampling, and the
challenge chosen answers require a format audit before another paid run. Preserve
both adapters and the complete report for offline diagnosis; do not launch the
verdict's generic follow-on intervention until that root-cause analysis is done.
