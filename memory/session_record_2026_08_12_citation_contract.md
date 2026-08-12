# R-F3910 — citation and contradiction contract repair

## Outcome

- Preserved `data/training/checkpoints/aria_tooluse_curve_sft_v5.tgz` as the
  sole eligible model parent. No GPU pod or paid training run was started.
- Added a machine-readable `citation_sources` allowlist to every JSON tool turn.
  Retrieval metadata such as `aria_search`, `credibility_tier`, and ARIA memory
  labels remains visible as metadata but is never citeable.
- Added a contradiction reference gate: a no-match sanctions screen may not be
  synthesized as an affirmative CLEAN verdict when adverse reporting exists.
- Applied both contracts to replay curricula and to the guarded future-capture
  writer, so the repair is structural rather than a one-off data rewrite.

## CPU evidence

- Persisted curriculum:
  `data/training/aria_tooluse_citation_contract_v8.jsonl` (350 rows, zero DPO).
- Manifest:
  `data/eval_reports/tooluse_citation_contract_v8_manifest.json`.
- Byte audit: 350 reference contracts valid, 468/468 tool turns carry explicit
  allowlists, zero contradiction targets contain `starting point is clean`.
- Strict preflight: 350 train / 168 eval, 65 / 50 disjoint entities, zero train
  overlap with 480 golden entities, 518/518 renders, maximum 2,795 / 4,096 tokens.
- Tests: 277 neighboring tool-use/corpus tests passed. Production compile gate:
  zero broken modules.
- Constitutional RAG query was attempted and reported DEGRADED because the local
  client was unavailable. The operator assigned that independent structural
  repair to Claude as R-F3911; no dependency was installed as a workaround.

## Binding next gate

The v8 file is CPU-approved training input, not a promoted model and not evidence
of behavioral improvement. Before any GPU spend, re-run the strict preflight on
the persisted bytes. Any future candidate must still preserve 30/30 calibration
and improve or preserve every axis on the unchanged n=168 held-out set.
