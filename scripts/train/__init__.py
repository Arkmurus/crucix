"""ARIA-LLM fine-tune harness — Phase 3 of the independence roadmap.

Scripts in this directory run on a rented GPU host (RunPod / Lambda
Labs / Fly.io GPU) when ARIA-LLM v0.1 is being trained. Not part of
the live ARIA service; invoked manually by the operator.

Workflow:
  1. prepare_sft.py  — convert /data/aria_training/harvest-*.jsonl
                        + curated content into SFT-ready dataset
  2. prepare_dpo.py  — convert R-F59 + R-F80 adversarial outputs into
                        preference pairs
  3. sft_train.py    — LoRA SFT on the SFT dataset
  4. dpo_train.py    — DPO on the preference pairs (after SFT)
  5. eval_aria_llm.py — run the 33-attack adversarial library +
                        defence-DD eval set against any checkpoint

See docs/aria_independence_roadmap.md §3 for the full Phase 3 protocol.
"""
