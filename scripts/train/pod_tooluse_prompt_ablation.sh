#!/usr/bin/env bash
# R-F4153 — evaluate one immutable adapter with and without a prompt policy.
set -uo pipefail

BASE_MODEL="${BASE_MODEL:-mistralai/Mistral-7B-Instruct-v0.3}"
ADAPTER="${ADAPTER:?need ADAPTER}"
EVAL_FILE="${EVAL_FILE:-/workspace/datasets/aria_tooluse_eval.jsonl}"
POLICY_FILE="${POLICY_FILE:-/workspace/datasets/resolution_prompt_policy_v1.txt}"
EXPECTED_ROWS="${EXPECTED_ROWS:-168}"
EXPECTED_POLICY_SHA256="${EXPECTED_POLICY_SHA256:?need EXPECTED_POLICY_SHA256}"
SCRIPTS=/workspace/crucix/scripts/train
EVALD=/workspace/eval
LOGS=/workspace/logs
PORT=8888
export HF_HOME=/workspace/.cache/huggingface
mkdir -p "$EVALD" "$LOGS"
rm -f "$EVALD/_cycle_status"

log(){ echo "[$(date -u +%H:%M:%S)] [prompt-ablation] $*"; }
fail(){ echo "[FATAL] $*" >&2; exit 1; }
on_exit(){ local rc=$?; echo "$rc" > "$EVALD/_cycle_status" 2>/dev/null || true; }
trap on_exit EXIT
require_watchdog(){
  [ -s "$EVALD/_watchdog_pid" ] \
    && kill -0 "$(cat "$EVALD/_watchdog_pid")" 2>/dev/null \
    || fail "self-stop watchdog unavailable"
}

[ -f "$ADAPTER/adapter_config.json" ] || fail "accepted adapter missing"
[ -s "$EVAL_FILE" ] || fail "held-out eval missing"
[ -s "$POLICY_FILE" ] || fail "prompt policy missing"
printf '%s  %s\n' "$EXPECTED_POLICY_SHA256" "$POLICY_FILE" | sha256sum -c - \
  || fail "prompt policy hash mismatch"
python - "$EVAL_FILE" "$EXPECTED_ROWS" <<'PY' || fail "held-out row count"
import sys
rows = [line for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
if len(rows) != int(sys.argv[2]):
    raise SystemExit(f"expected {sys.argv[2]} rows, got {len(rows)}")
PY
require_watchdog

log "installing pinned evaluation runtime"
pip install -q "transformers==4.46.3" "peft==0.13.2" \
  "accelerate>=0.34" bitsandbytes sentencepiece protobuf fastapi uvicorn httpx \
  || fail "dependency installation failed"
python - <<'PY' || fail "CUDA bf16 runtime unavailable"
import torch
if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
    raise SystemExit("required CUDA bf16 runtime unavailable")
PY
require_watchdog

ADAPTER="$ADAPTER" MODEL_NAME=aria-tooluse-parent PORT=$PORT BASE_MODEL="$BASE_MODEL" \
  setsid nohup python "$SCRIPTS/serve_eval_shim.py" >"$LOGS/prompt_ablation_shim.log" 2>&1 </dev/null &
for i in $(seq 1 60); do
  curl -fsS --max-time 5 "http://localhost:$PORT/v1/models" | grep -q aria-tooluse-parent && break
  [ "$i" -eq 60 ] && fail "evaluation shim unavailable"
  sleep 10
done
require_watchdog

log "baseline arm: unchanged held-out prompts"
python -m scripts.train.eval_tooluse --target "http://localhost:$PORT/v1" \
  --model aria-tooluse-parent --eval-file "$EVAL_FILE" \
  --out "$EVALD/aria_tooluse_resolution_prompt_ablation_v1_baseline.json" \
  2>&1 | tee "$LOGS/prompt_ablation_baseline.log" || fail "baseline arm failed"
require_watchdog

log "policy arm: identical weights and held-out rows"
python -m scripts.train.eval_tooluse --target "http://localhost:$PORT/v1" \
  --model aria-tooluse-parent --eval-file "$EVAL_FILE" \
  --system-append-file "$POLICY_FILE" \
  --out "$EVALD/aria_tooluse_resolution_prompt_ablation_v1_policy.json" \
  2>&1 | tee "$LOGS/prompt_ablation_policy.log" || fail "policy arm failed"

python - "$EVALD" "$EXPECTED_ROWS" "$EXPECTED_POLICY_SHA256" <<'PY' \
  || fail "complete paired-report proof failed"
import json, sys
from pathlib import Path
root, expected, policy_sha = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
empty_sha = __import__("hashlib").sha256(b"").hexdigest()
for arm, wanted in (("baseline", empty_sha), ("policy", policy_sha)):
    path = root / f"aria_tooluse_resolution_prompt_ablation_v1_{arm}.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("complete") is not True or report.get("total") != expected:
        raise SystemExit(f"{arm} report incomplete")
    if len(report.get("rows") or []) != expected:
        raise SystemExit(f"{arm} row evidence incomplete")
    if (report.get("run") or {}).get("system_append_sha256") != wanted:
        raise SystemExit(f"{arm} policy fingerprint mismatch")
print(f"verified paired immutable ablation: n={expected} per arm")
PY
pkill -f serve_eval_shim 2>/dev/null || true
log "paired evaluation complete"
