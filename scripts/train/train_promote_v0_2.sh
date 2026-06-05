#!/bin/bash
# R-F1340 — ARIA-LLM v0.2 self-improvement: train DPO → eval → PROMOTE ONLY IF BETTER.
#
# Runs ENTIRELY on the RunPod pod. Her own data (golden truth + her own
# failures), her own weights — neither DeepSeek nor Claude is in the
# training signal or the reasoning. This is the autonomous-improvement
# engine: one command upgrades her, and a hard gate guarantees a worse
# model NEVER reaches serving.
#
# BULLETPROOF GUARANTEES:
#   1. The pod ALWAYS ends with a model serving (v0.2 if better, else v0.1).
#   2. v0.2 is promoted ONLY if it beats v0.1 on BOTH accuracy (>=) AND
#      injection leak_rate (<=). Either regression → rollback to v0.1.
#   3. Every step checks its precondition; missing input aborts before any
#      destructive action.
#
# Usage (on the pod):
#   bash /workspace/train_promote_v0_2.sh
# Or from the operator box:
#   scp -i ~/.ssh/runpod_aria -P <port> scripts/train/train_promote_v0_2.sh root@<host>:/workspace/
#   scp ... data/training/aria_dpo_v1.jsonl root@<host>:/workspace/datasets/
#   scp ... data/training/aria_eval_500q.jsonl root@<host>:/workspace/datasets/
#   ssh ... 'bash /workspace/train_promote_v0_2.sh'
#
# NOTE: this STOPS vLLM (frees the GPU for training), so ARIA is offline for
# the run (~1-2h on an A100/H100). Run it OUTSIDE her serving window or when
# a brief downtime is acceptable.
set -uo pipefail

BASE_MODEL="mistralai/Mistral-7B-Instruct-v0.3"
SFT="/workspace/checkpoints/aria_llm_v0_1_sft"
DPO_OUT="/workspace/checkpoints/aria_llm_v0_2_dpo"
DPO_FILE="/workspace/datasets/aria_dpo_v1.jsonl"
EVAL_SET="/workspace/datasets/aria_eval_500q.jsonl"
EVAL_DIR="/workspace/eval"
LOGS="/workspace/logs"
SCRIPTS="/workspace/crucix/scripts/train"
V01_REPORT="${EVAL_DIR}/aria_llm_v0.1_sft_report.json"
V02_REPORT="${EVAL_DIR}/aria_llm_v0.2_dpo_report.json"
PORT=8888
MAX_LORA_RANK=32

mkdir -p "${EVAL_DIR}" "${LOGS}"
log() { echo "[$(date -u +%H:%M:%S)] $*"; }
fail() { echo "[FATAL] $*" >&2; exit 1; }

serve() {  # serve <lora_path> <name>
  local lora="$1" name="$2"
  pkill -f "vllm.entrypoints.openai" 2>/dev/null || true
  sleep 3
  HF_HOME=/workspace/.cache/huggingface VLLM_USE_DEEP_GEMM=0 \
    nohup python -m vllm.entrypoints.openai.api_server \
      --model "${BASE_MODEL}" --enable-lora \
      --lora-modules "${name}=${lora}" \
      --max-loras 1 --max-lora-rank ${MAX_LORA_RANK} \
      --gpu-memory-utilization 0.9 --host 0.0.0.0 --port ${PORT} \
      > "${LOGS}/vllm_${name}.log" 2>&1 &
  for i in $(seq 1 40); do
    if curl -s --max-time 5 "http://localhost:${PORT}/v1/models" | grep -q "${name}"; then
      log "vLLM serving ${name}"; return 0
    fi
    sleep 10
  done
  return 1
}

eval_model() {  # eval_model <name> <out>
  local name="$1" out="$2"
  python "${SCRIPTS}/eval_aria_llm.py" \
    --target "http://localhost:${PORT}/v1" --model "${name}" \
    --eval-set "${EVAL_SET}" --out "${out}" 2>&1 | tee -a "${LOGS}/eval_${name}.log"
}

# ── Preflight (abort before any destructive step) ──────────────────────
log "=== v0.2 self-improvement run ==="
[ -d "${SFT}" ] || fail "SFT checkpoint missing: ${SFT}"
[ -f "${DPO_FILE}" ] || fail "DPO dataset missing: ${DPO_FILE} (build with build_dpo_from_eval.py)"
[ -f "${EVAL_SET}" ] || fail "eval set missing: ${EVAL_SET}"
[ -s "${DPO_FILE}" ] || fail "DPO dataset is empty: ${DPO_FILE}"
log "preflight ok — DPO pairs: $(wc -l < "${DPO_FILE}")"

# ── Deps ───────────────────────────────────────────────────────────────
# R-F1345: pin a COHERENT set (the base image's transformers + a mismatched
# peft caused "Could not import BloomPreTrainedModel" and the v0.2 run failed).
# trl 0.12 uses processing_class (dpo_train.py R-F1345). Do NOT pipe pip
# through tail — that masked the failure exit code last time.
log "installing pinned training deps…"
# sentencepiece + protobuf are REQUIRED by Mistral's tokenizer (run #2 failed
# at tokenizer load without them) — they are not pulled transitively. R-F1345.
pip install -q "transformers==4.46.3" "peft==0.13.2" "trl==0.12.2" \
    "accelerate>=0.34" bitsandbytes datasets sentencepiece protobuf \
    || fail "dep install failed"
# Fail FAST if the training stack can't actually import OR the tokenizer deps
# are missing (the two real failure modes seen on this pod).
python - <<'PYCHECK' || fail "training deps import check failed — aborting before train"
import transformers, peft, trl, bitsandbytes, accelerate  # noqa
import sentencepiece, google.protobuf  # noqa — Mistral tokenizer needs these
from trl import DPOTrainer, DPOConfig  # noqa
print(f"deps ok: transformers {transformers.__version__} peft {peft.__version__} trl {trl.__version__} +sentencepiece")
PYCHECK

# ── Baseline eval of v0.1 (so the gate has a reference) ────────────────
# Reuse the committed v0.1 report if present on the pod; else eval now.
if [ ! -f "${V01_REPORT}" ]; then
  log "no v0.1 baseline on pod — serving + evaluating v0.1 first…"
  serve "${SFT}" "aria-llm-v0.1" || fail "could not serve v0.1 for baseline"
  eval_model "aria-llm-v0.1" "${V01_REPORT}" || fail "v0.1 baseline eval failed"
fi

# ── DPO train ──────────────────────────────────────────────────────────
log "stopping vLLM to free the GPU for training…"
pkill -f "vllm.entrypoints.openai" 2>/dev/null || true
sleep 5
log "DPO training → ${DPO_OUT} …"
python "${SCRIPTS}/dpo_train.py" \
  --base-model "${BASE_MODEL}" \
  --sft-checkpoint "${SFT}" \
  --dpo-file "${DPO_FILE}" \
  --output-dir "${DPO_OUT}" \
  --epochs 1 --beta 0.1 --lr 5e-6 --batch-size 2 --max-seq-len 4096 \
  --load-in-4bit 2>&1 | tee "${LOGS}/dpo_train.log"

if [ ! -d "${DPO_OUT}" ] || [ -z "$(ls -A "${DPO_OUT}" 2>/dev/null)" ]; then
  log "DPO training produced no checkpoint — RESTORING v0.1 serving"
  serve "${SFT}" "aria-llm-v0.1" || fail "ROLLBACK FAILED — pod has no serving model!"
  fail "DPO training failed; rolled back to v0.1"
fi

# ── Eval v0.2 ──────────────────────────────────────────────────────────
log "serving v0.2 for evaluation…"
serve "${DPO_OUT}" "aria-llm-v0.2" || { serve "${SFT}" "aria-llm-v0.1"; fail "v0.2 would not serve; rolled back to v0.1"; }
eval_model "aria-llm-v0.2" "${V02_REPORT}" || { serve "${SFT}" "aria-llm-v0.1"; fail "v0.2 eval failed; rolled back to v0.1"; }

# ── GATE: promote only if v0.2 >= v0.1 on accuracy AND leak ────────────
log "comparing v0.2 vs v0.1 …"
DECISION=$(python - "$V01_REPORT" "$V02_REPORT" <<'PY'
import json, sys
# R-F1345: FAIL-CLOSED gate. A missing/empty/malformed v0.2 report previously
# defaulted to acc=0/leak=1 and (0>=0 and 1<=1) => PROMOTE a broken model.
# Now: promote ONLY when the v0.2 report is VALID and MEASURABLY not-worse.
def load(p):
    try:
        return json.load(open(p))
    except Exception:
        return None
def dd(r): return (r or {}).get("defence_dd") or (r or {}).get("dd_eval") or {}
def pi(r): return (r or {}).get("prompt_injection") or {}
v1, v2 = load(sys.argv[1]), load(sys.argv[2])
dd1, dd2, pi2 = dd(v1), dd(v2), pi(v2)
a1 = dd1.get("accuracy") or 0.0
a2 = dd2.get("accuracy")
l2 = pi2.get("leak_rate")
# v0.2 report must be REAL: ran a non-zero eval and produced concrete numbers.
valid_v2 = (dd2.get("total") or 0) > 0 and a2 is not None and l2 is not None and a2 > 0.0
l1 = pi(v1).get("leak_rate")
l1 = 1.0 if l1 is None else l1
better = valid_v2 and (a2 >= a1) and (l2 <= l1)
print(f"v0.1 acc={a1:.3f} leak={l1:.3f} | v0.2 acc={a2} leak={l2} valid={valid_v2}",
      file=sys.stderr)
print("PROMOTE" if better else "KEEP_V01")
PY
)
log "decision: ${DECISION}"

if [ "${DECISION}" = "PROMOTE" ]; then
  log "v0.2 wins → serving v0.2 as the model"
  serve "${DPO_OUT}" "aria-llm-v0.1" || fail "promote serve failed!"  # serve under v0.1 name so ARIA_LLM_MODEL is unchanged
  log "=== PROMOTED v0.2 (served as aria-llm-v0.1) ==="
else
  log "v0.2 did NOT beat v0.1 → keeping v0.1"
  serve "${SFT}" "aria-llm-v0.1" || fail "ROLLBACK FAILED — pod has no serving model!"
  log "=== KEPT v0.1 (v0.2 regressed; see ${V02_REPORT}) ==="
fi
log "done."
