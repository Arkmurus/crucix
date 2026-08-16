#!/usr/bin/env bash
set -uo pipefail
BASE_MODEL="mistralai/Mistral-7B-Instruct-v0.3"
PROBE=/workspace/datasets/aria_tooluse_curve_probe.jsonl
BEFORE=/workspace/eval/aria_tooluse_curve_raw_probe.json
EVAL=/workspace/datasets/aria_tooluse_eval.jsonl
REPORT=/workspace/eval/aria_tooluse_dpo_eval.json
ARCHIVE=/workspace/eval/aria_tooluse_dpo_adapter.tgz
AFTER=/workspace/eval/aria_tooluse_sft_child_probe.json
VERDICT=/workspace/eval/aria_tooluse_sft_child_verdict.json
DIAGNOSTICS=/workspace/eval/aria_tooluse_curve_diagnostics.tgz
SCRIPTS=/workspace/crucix/scripts/train
PORT=8888
export HF_HOME=/workspace/.cache/huggingface
cd /workspace/crucix || exit 1
fail(){ echo "[FATAL] $*" >&2; exit 1; }
collect(){ tar -czf "$DIAGNOSTICS" -C /workspace/eval aria_tooluse_sft_child_probe.json aria_tooluse_sft_child_verdict.json 2>/dev/null || true; }
on_exit(){ rc=$?; collect; echo "$rc" > /workspace/eval/_cycle_status; }
trap on_exit EXIT
[ -f "$SFT_ADAPTER/adapter_config.json" ] || fail "preserved adapter missing"
tar -czf "$ARCHIVE" -C "$(dirname "$SFT_ADAPTER")" "$(basename "$SFT_ADAPTER")" || fail "archive"
pip install -q "transformers==4.46.3" "peft==0.13.2" "trl==0.12.2" "accelerate>=0.34" bitsandbytes datasets sentencepiece protobuf fastapi uvicorn httpx || fail "dependencies"
ADAPTER="$SFT_ADAPTER" MODEL_NAME=aria-tooluse-sft-child PORT=$PORT BASE_MODEL="$BASE_MODEL" setsid nohup python "$SCRIPTS/serve_eval_shim.py" >/workspace/logs/recovery_shim.log 2>&1 </dev/null &
for i in $(seq 1 60); do
  if python -c "import urllib.request; d=urllib.request.urlopen('http://localhost:$PORT/v1/models',timeout=5).read(); assert b'aria-tooluse-sft-child' in d" 2>/dev/null; then break; fi
  [ "$i" -lt 60 ] || fail "shim unavailable"
  sleep 10
done
python -m scripts.train.eval_tooluse --target "http://localhost:$PORT/v1" --model aria-tooluse-sft-child --eval-file "$PROBE" --out "$AFTER" || fail "calibration eval"
python -m scripts.train.learning_curve_gate --before "$BEFORE" --after "$AFTER" --verdict-out "$VERDICT" --protected-axis tooluse_adverse --protected-axis tooluse_contradiction --protected-axis tooluse_news_impact --protected-axis tooluse_resolution || fail "calibration gate"
collect
python -m scripts.train.eval_tooluse --target "http://localhost:$PORT/v1" --model aria-tooluse-sft-child --eval-file "$EVAL" --out "$REPORT" || fail "held-out eval"
