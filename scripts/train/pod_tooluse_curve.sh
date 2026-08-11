#!/usr/bin/env bash
# R-F3848 — staged positive-curve SFT→DPO cycle.
set -uo pipefail
BASE_MODEL="${BASE_MODEL:-mistralai/Mistral-7B-Instruct-v0.3}"
SFT_FILE=/workspace/datasets/aria_tooluse_retention_sft.jsonl
DPO_FILE=/workspace/datasets/aria_tooluse_dpo_v3.jsonl
PROBE_FILE=/workspace/datasets/aria_tooluse_curve_probe.jsonl
RAW_PROBE=/workspace/eval/aria_tooluse_curve_raw_probe.json
EVAL_FILE=/workspace/datasets/aria_tooluse_eval.jsonl
SFT_OUT=/workspace/checkpoints/aria_tooluse_curve_sft
DPO_OUT="${DPO_OUT:-/workspace/checkpoints/aria_tooluse_curve_dpo}"
SFT_ARCHIVE=/workspace/eval/aria_tooluse_mixed_sft.tgz
DPO_ARCHIVE=/workspace/eval/aria_tooluse_dpo_adapter.tgz
REPORT=/workspace/eval/aria_tooluse_dpo_eval.json
DIAGNOSTICS=/workspace/eval/aria_tooluse_curve_diagnostics.tgz
SCRIPTS=/workspace/crucix/scripts/train; LOGS=/workspace/logs; PORT=8888
export HF_HOME=/workspace/.cache/huggingface
cd /workspace/crucix || exit 1
mkdir -p /workspace/checkpoints /workspace/eval "$LOGS"
rm -f /workspace/eval/_cycle_status
log(){ echo "[$(date -u +%H:%M:%S)] [tooluse-curve] $*"; }
fail(){ echo "[FATAL] $*" >&2; exit 1; }
collect_diagnostics(){
  local files=() name
  for name in aria_tooluse_curve_raw_probe.json aria_tooluse_curve_sft_probe.json aria_tooluse_curve_dpo_probe.json aria_tooluse_curve_sft_verdict.json aria_tooluse_curve_dpo_verdict.json; do
    [ ! -f "/workspace/eval/$name" ] || files+=("$name")
  done
  if [ "${#files[@]}" -gt 0 ]; then
    tar -czf "$DIAGNOSTICS.tmp" -C /workspace/eval "${files[@]}" && mv "$DIAGNOSTICS.tmp" "$DIAGNOSTICS"
  fi
}
trap 'rc=$?; collect_diagnostics 2>/dev/null || true; echo "$rc" > /workspace/eval/_cycle_status 2>/dev/null || true' EXIT
require_watchdog(){ [ -s /workspace/eval/_watchdog_pid ] && kill -0 "$(cat /workspace/eval/_watchdog_pid)" 2>/dev/null || fail "self-stop watchdog unavailable"; }
validate_count(){ python - "$1" "$2" <<'PY'
import json,sys
r=[json.loads(x) for x in open(sys.argv[1],encoding="utf-8") if x.strip()]
assert len(r)==int(sys.argv[2]), (len(r),sys.argv[2])
PY
}
for f in "$SFT_FILE" "$DPO_FILE" "$PROBE_FILE" "$RAW_PROBE" "$EVAL_FILE"; do [ -s "$f" ] || fail "missing $f"; done
validate_count "$SFT_FILE" 90 || fail "SFT count"
validate_count "$DPO_FILE" 47 || fail "DPO count"
validate_count "$PROBE_FILE" 30 || fail "probe count"
validate_count "$EVAL_FILE" 168 || fail "eval count"
pip install -q "transformers==4.46.3" "peft==0.13.2" "trl==0.12.2" "accelerate>=0.34" bitsandbytes datasets sentencepiece protobuf fastapi uvicorn httpx || fail "dependencies"
python - <<'PY' || fail "runtime"
import torch,transformers,peft,trl
assert torch.cuda.is_available() and torch.cuda.is_bf16_supported()
assert (transformers.__version__,peft.__version__,trl.__version__)==("4.46.3","0.13.2","0.12.2")
PY
evaluate(){
  local adapter="$1" model="$2" file="$3" out="$4"
  ADAPTER="$adapter" MODEL_NAME="$model" PORT=$PORT BASE_MODEL="$BASE_MODEL" setsid nohup python "$SCRIPTS/serve_eval_shim.py" >"$LOGS/${model}_shim.log" 2>&1 </dev/null &
  for i in $(seq 1 60); do curl -fsS --max-time 5 "http://localhost:$PORT/v1/models" | grep -q "$model" && break; [ "$i" -eq 60 ] && fail "shim $model"; sleep 10; done
  python -m scripts.train.eval_tooluse --target "http://localhost:$PORT/v1" --model "$model" --eval-file "$file" --out "$out" 2>&1 | tee "$LOGS/${model}_eval.log" || fail "eval $model"
  pkill -f serve_eval_shim 2>/dev/null || true; sleep 2
}
curve_gate(){ python "$SCRIPTS/learning_curve_gate.py" --before "$1" --after "$2" --verdict-out "$3" --protected-axis tooluse_adverse --protected-axis tooluse_contradiction --protected-axis tooluse_news_impact --protected-axis tooluse_resolution; }
archive(){ tar --exclude='checkpoint-*' -czf "$2.tmp" -C "$(dirname "$1")" "$(basename "$1")" && tar -tzf "$2.tmp" | awk '/\/adapter_config.json$/ {f=1} END {exit !f}' && mv "$2.tmp" "$2"; }
require_watchdog
python "$SCRIPTS/sft_train.py" --base-model "$BASE_MODEL" --train-file "$SFT_FILE" --output-dir "$SFT_OUT" --epochs 1 --lora-rank 32 --lora-alpha 64 --lr 2e-5 --batch-size 2 --max-seq-len 4096 --load-in-4bit 2>&1 | tee "$LOGS/tooluse_curve_sft.log" || fail "SFT"
archive "$SFT_OUT" "$SFT_ARCHIVE" || fail "SFT archive"
evaluate "$SFT_OUT" aria-curve-sft "$PROBE_FILE" /workspace/eval/aria_tooluse_curve_sft_probe.json
curve_gate "$RAW_PROBE" /workspace/eval/aria_tooluse_curve_sft_probe.json /workspace/eval/aria_tooluse_curve_sft_verdict.json || fail "raw-to-SFT curve gate"
collect_diagnostics
require_watchdog
python "$SCRIPTS/dpo_train.py" --base-model "$BASE_MODEL" --sft-checkpoint "$SFT_OUT" --dpo-file "$DPO_FILE" --output-dir "$DPO_OUT" --epochs 1 --beta 0.3 --lr 2e-6 --batch-size 2 --gradient-accumulation-steps 1 --max-seq-len 4096 --max-grad-norm 0.3 --load-in-4bit 2>&1 | tee "$LOGS/tooluse_dpo_train.log" || fail "DPO"
archive "$DPO_OUT" "$DPO_ARCHIVE" || fail "DPO archive"
evaluate "$DPO_OUT" aria-curve-dpo "$PROBE_FILE" /workspace/eval/aria_tooluse_curve_dpo_probe.json
curve_gate /workspace/eval/aria_tooluse_curve_sft_probe.json /workspace/eval/aria_tooluse_curve_dpo_probe.json /workspace/eval/aria_tooluse_curve_dpo_verdict.json || fail "SFT-to-DPO curve gate"
collect_diagnostics
require_watchdog
evaluate "$DPO_OUT" aria-tooluse-dpo "$EVAL_FILE" "$REPORT"
log "positive-curve cycle complete"
