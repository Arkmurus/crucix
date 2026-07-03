# Eval-contaminated training data — DO NOT TRAIN (R-F2367, 2026-07-03)

These DPO files were built with the frozen 500-Q eval set (Phase-A gate #6) as the
DPO PROMPTS (root generator: scripts/train/build_dpo_from_eval.py). Audit found:
- aria_dpo_v1.jsonl   : 396/396  (100%) prompts are eval questions
- aria_dpo_v04.jsonl  : 350/356  (98.3%) prompts are eval questions

Training on these teaches the model the eval questions -> the 500-Q eval stops
being a held-out measure and any gate-#2 lift is fraudulent (§24 cancel condition).
Preserved (not deleted, §7) as the audit trail. The pre-flight guard
(scripts/train/preflight_eval_contamination.py) now blocks any cycle from using
them. Use CLEAN DPO instead: aria_dpo_pairs_v1_str.jsonl / aria_citation_dpo_v2.jsonl.
