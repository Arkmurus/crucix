# ★ V0.3 TRAINING RUN — booked for TOMORROW (2026-06-10), greenlit by operator

First REAL training cycle: SFT-distillation of DeepSeek into ARIA's own 7B → v0.3 →
eval vs v0.2 on a TRUSTWORTHY judge. The first honest "did the number go up?" test.

## DECISION (Claude's call): 500-pair corpus, NOT proof-size 100
100 SFT pairs won't move v0.2 meaningfully — a flat result would waste the pod day.
Generation is the cheap no-pod part, so we build 500 tonight and spend the pod on a
meaningful train+eval.

## WHAT'S READY (no pod, done tonight)
- ✅ Judge VALIDATED (R-F1456..1468) — grades correct/partial/wrong sensibly, catches
  fabrication + factual errors. expected_answer is now in the eval set (re-exported).
- ✅ Live DeepSeek clients (R-F1469) — data engine produces real, contamination-checked
  distillation pairs.
- ✅ Corpus: data/training/aria_sft_distill_batch1.jsonl (100) + batch2.jsonl (400,
  generating ~tonight). COMBINE before the run:
    cat batch1 batch2 > data/training/aria_sft_distill_500.jsonl
  (messages format: {"messages":[{user},{assistant}]} — feeds sft_train.py --train-file directly)

## THE RUN — now ONE COMMAND (R-F1470 driver built; pre-flight DONE 2026-06-09 eve)
Base = Mistral-7B-Instruct-v0.3 (v0.2's base; serve from the volume HF cache, HF_HUB_OFFLINE).

  bash scripts/train/run_v0_3_cycle.sh

The driver (local orchestrator `run_v0_3_cycle.sh`, mirrors the proven R-F1463 pattern)
does everything: start pod lqhxb4swwafuzv (dynamic SSH port via portMappings["22"]) →
scp the 500 corpus + the CURRENT sft_train/serve_eval_shim/eval_aria_llm scripts + the
on-pod driver `v0_3_pod_run.sh` + the 500-Q eval set → run train→serve→eval→verdict ON the
pod → pull reports → print PROMOTE/KEEP verdict → STOP the pod (EXIT trap).

On-pod driver `v0_3_pod_run.sh` (mirrors train_promote_v0_2.sh + baseline_pod_run.sh):
1. Pinned coherent deps (transformers==4.46.3 / peft==0.13.2 / trl==0.12.2 + datasets etc.).
2. SFT TRAIN sft_train.py --base-model Mistral-7B-Instruct-v0.3 --train-file ...500.jsonl
   --output-dir /workspace/checkpoints/aria_llm_v0_3_sft --epochs 3 --load-in-4bit
   (HF_HOME=/workspace/.cache/huggingface HF_HUB_OFFLINE=1).
3. Serve v0.3 via serve_eval_shim.py (NOT vLLM — driver too old → EngineCore crash, R-F1455)
   → 3-prompt coherence smoke (abort if degenerate, ~$0.10) → eval v0.3 on 500-Q (judge auto-on).
4. Serve v0.2 (existing DPO adapter) → eval on the SAME 500-Q + judge (apples-to-apples).
5. DeepSeek baseline (optional) → verdict: PROMOTE v0.3 only if judge-DD ≥ v0.2 AND leak ≤ v0.2.
6. Per-call timeouts (R-F1468) prevent the hang that bit the judge run; EXIT trap stops the pod.

★ FIXED IN PRE-FLIGHT (would have wasted the cycle): sft_train.py `_format_chat` indexed
record["input"]/["output"] unconditionally → KeyError on the messages-format corpus AFTER
the paid base load. R-F1470 makes it accept BOTH messages-format (our corpus) and legacy
input/output. Test: aria_service/tests/test_rf1470_sft_format.py (4/4). The earlier claim
"messages format feeds sft_train.py directly" was WRONG — now it actually does.

## VERDICT (Friday-style)
v0.3 judge-DD vs v0.2 judge-DD on the same 500-Q + validated judge. PROMOTE only if v0.3 ≥ v0.2.
Remember the gold rewards ARIA's ground-or-abstain doctrine — interpret accordingly.

## PRE-FLIGHT TODO before the run — DONE 2026-06-09 eve (R-F1470)
- [x] batch2 generated (DeepSeek distillation, contamination + cross-batch deduped vs batch1)
      via scripts/train/generate_distill_batch.py → 399 pairs.
- [x] Combined batch1(100)+batch2(399) → 499; whole-corpus contamination re-check vs the frozen
      500-Q = 0 leakage.
- [x] §24 DATASET-QUALITY GATE (scripts/train/self_critique_sample.py): DeepSeek self-critique on
      all 499 → fabrication 2.0% + ERROR 11.0% = 13.1% factual-defect. Operator chose FILTER →
      dropped 65 FABRICATION/ERROR pairs → **data/training/aria_sft_distill_500.jsonl = 434 CLEAN
      pairs** (unfiltered preserved as ..._prefilter.jsonl; report data/eval_reports/self_critique_full_499.json).
      Caveat: DeepSeek-critiquing-DeepSeek (imperfect, self-bias) but it flagged its OWN errors;
      thin spot: dual_use 5→2 (covered by export_control/weapons_proliferation). The 434 is the
      v0.3 SFT corpus. Per-topic: ~20-27 each.
- [x] Pod driver written + syntax-checked: scripts/train/run_v0_3_cycle.sh (local) +
      scripts/train/v0_3_pod_run.sh (on-pod). Shim path, dynamic port, EXIT trap, HF volume cache.
- [x] FIXED the sft_train.py messages-format KeyError (would have crashed AFTER the paid load).
- [x] pair_builder require_judge_correct already defaults False (R-F1468); our corpus is raw, n/a.
- ACLED etc. irrelevant.

## TOMORROW AM (2026-06-10): just run it
  bash scripts/train/run_v0_3_cycle.sh
Operator confirms the pod (lqhxb4swwafuzv) is available; ~$5-10, ~1-1.5h; the driver stops the pod.

## NOTE on quality (batch 2+)
Batch 1/2 are RAW DeepSeek distillation — DeepSeek does fabricate (we saw it fabricate Saudi
sources + get Mozambique/Wassenaar wrong in the eval). For the FIRST cycle this is acceptable
(bootstrap). Before scaling to 1000s, add ARIA's SELF-CRITIQUE quality gate (NOT self-grading).

Status: corpus building tonight; pod run greenlit for tomorrow AM. — Claude, 2026-06-09 eve
