#!/usr/bin/env bash
# EVAL-ONLY v0.7 via ON-DEMAND CREATE (R-F17xx) — beats the US-KS-2 capacity block.
# The single stopped pod (2adkzeri6fa2zi) is pinned to a full host. Instead, create
# a FRESH on-demand SECURE pod on the SAME network volume (4vdw2zmqov, US-KS-2) across
# all GPU types that exist in that DC — RunPod places it on any host with capacity.
# The v0.7 adapter lives on the volume (/workspace/checkpoints/aria_llm_v0_4_sft), so a
# new pod can serve it. Serves + evals (NO retrain), pulls the report, STOPS the pod.
# Does NOT set ARIA_LLM_URL.
set -uo pipefail
# R-F4305 (C-258) — resolve the repo from THIS script, never a hardcoded
# checkout. The old hardcoded literal named a machine that no longer exists,
# and `cd` to a missing dir under `set -uo pipefail` does NOT abort — the
# script silently continues in the wrong directory. git first; BASH_SOURCE
# fallback because this file is rsynced onto pods where there is no .git.
# NOTE the braces: `A || B && C` parses as `(A || B) && C`, so an ungrouped
# fallback runs `pwd` even when git SUCCEEDS and $REPO gets two lines.
REPO="$(git rev-parse --show-toplevel 2>/dev/null || { cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd; })"
cd "$REPO" || { echo "FATAL: cannot resolve repo root" >&2; exit 1; }
API="https://rest.runpod.io/v1"
KEY=$(grep -E '^RUNPOD_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
DSK=$(grep -E '^DEEPSEEK_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
VOL=4vdw2zmqov
GPUS='["NVIDIA A40","NVIDIA L40S","NVIDIA RTX A6000","NVIDIA L40","NVIDIA RTX 6000 Ada Generation","NVIDIA A100 80GB PCIe","NVIDIA A100-SXM4-80GB"]'
RETRY_SECS=${RETRY_SECS:-120}
MAX_TRIES=${MAX_TRIES:-25}
EVAL_LOCAL="data/eval_reports/aria_eval_500q_openbook.jsonl"
[ -s "$EVAL_LOCAL" ] || { echo "[ev] FATAL: eval set missing"; exit 1; }
KEYF="/tmp/rpkey_ev"; cp ~/.ssh/runpod_aria "$KEYF"; chmod 600 "$KEYF"
SSH="ssh -i $KEYF -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=6"
TSSH(){ timeout 75 $SSH "$@"; }
jget(){ python -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1','') or '')" 2>/dev/null; }
pmget(){ python -c "import sys,json;d=json.load(sys.stdin);pm=d.get('portMappings') or {};print(pm.get('22') or '')" 2>/dev/null; }
log(){ echo "[$(date -u +%H:%M:%S)] $*"; }

# Create-loop: reuse an existing RUNNING pod on the volume, else create on-demand.
POD_ID=""
for i in $(seq 1 "$MAX_TRIES"); do
  FOUND=$(python scripts/train/_find_running_pod.py 2>/dev/null | head -1 | tr -d '[:space:]')
  if [ -n "$FOUND" ]; then POD_ID="$FOUND"; log "reusing RUNNING pod $POD_ID"; break; fi
  R=$(curl -s -X POST "$API/pods" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
        -d "{\"name\":\"aria-v07-eval\",\"imageName\":\"runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04\",
             \"gpuTypeIds\":$GPUS,\"gpuCount\":1,\"cloudType\":\"SECURE\",
             \"networkVolumeId\":\"$VOL\",\"volumeMountPath\":\"/workspace\",
             \"containerDiskInGb\":80,\"ports\":[\"8888/http\",\"22/tcp\"]}")
  POD_ID=$(echo "$R" | jget id)
  if [ -n "$POD_ID" ]; then log "CREATED on-demand pod $POD_ID (try $i/$MAX_TRIES)"; break; fi
  ERR=$(echo "$R" | python -c "import sys,json
try: print((json.load(sys.stdin).get('error','') or '')[:70])
except: print('')" 2>/dev/null)
  log "[try $i/$MAX_TRIES] no capacity ($ERR) — retry ${RETRY_SECS}s"; sleep "$RETRY_SECS"
done
[ -n "$POD_ID" ] || { log "GAVE UP — US-KS-2 capacity-blocked across all GPU types. Needs operator console migration."; exit 2; }

stop_pod(){ log "stopping pod $POD_ID"; curl -s -X POST "$API/pods/$POD_ID/stop" -H "Authorization: Bearer $KEY" >/dev/null 2>&1 || true; }
trap stop_pod EXIT

# Wait RUNNING + HOST:PORT
HOST=""; PORT=""
for j in $(seq 1 30); do
  PD=$(curl -s "$API/pods/$POD_ID" -H "Authorization: Bearer $KEY")
  ST=$(echo "$PD" | jget desiredStatus); HOST=$(echo "$PD" | jget publicIp); PORT=$(echo "$PD" | pmget)
  [ "$ST" = "RUNNING" ] && [ -n "$HOST" ] && [ -n "$PORT" ] && { log "RUNNING $HOST:$PORT"; break; }
  sleep 10
done
[ -n "$HOST" ] && [ -n "$PORT" ] || { log "FATAL: pod not RUNNING"; exit 1; }

# Stable SSH + settle
ok=0; for j in $(seq 1 40); do
  if TSSH -p "$PORT" root@"$HOST" "echo ok" 2>/dev/null | grep -q ok; then ok=$((ok+1)); else ok=0; fi
  [ "$ok" -ge 3 ] && { log "SSH stable"; break; }; sleep 5
done
sleep 15
RSCP(){ local s="$1" d="$2" t; for t in 1 2 3 4 5; do
  timeout 90 scp -i "$KEYF" -o StrictHostKeyChecking=no -o ConnectTimeout=15 -P "$PORT" "$s" root@"$HOST":"$d" 2>/dev/null && return 0
  log "scp retry $t $(basename "$s")"; sleep 10; done; return 1; }

# Push + arm watcher + run
for j in 1 2 3; do TSSH -p "$PORT" root@"$HOST" "mkdir -p /workspace/datasets /workspace/crucix/scripts/train /workspace/crucix/aria_service/intel /workspace/logs" 2>/dev/null && break; sleep 8; done
RSCP scripts/train/pod_eval_only.sh         /workspace/                       || { log "FATAL scp runner"; exit 1; }
RSCP scripts/train/pod_selfstop_watch_v04.sh /workspace/                      || { log "FATAL scp watcher"; exit 1; }
for f in serve_eval_shim.py eval_aria_llm.py; do RSCP "scripts/train/$f" "/workspace/crucix/scripts/train/$f" || { log "FATAL scp $f"; exit 1; }; done
RSCP "$EVAL_LOCAL" /workspace/datasets/aria_eval_openbook.jsonl || { log "FATAL scp eval set"; exit 1; }
for f in aria_service/__init__.py aria_service/intel/__init__.py aria_service/intel/engine_wiring.py aria_service/intel/prompt_injection_suite.py; do
  RSCP "$f" "/workspace/crucix/$f" || log "(opt dep $f not pushed)"; done

log "ARMING self-stop watcher…"
TSSH -p "$PORT" root@"$HOST" "POD_ID=$POD_ID RP_KEY='$KEY' GRACE=900 setsid nohup bash /workspace/pod_selfstop_watch_v04.sh >/workspace/logs/_selfstop.log 2>&1 </dev/null & echo ARMED" || log "WARN watcher arm failed"
log "launching eval-only DETACHED…"
TSSH -p "$PORT" root@"$HOST" "rm -f /workspace/eval/_cycle_status; DEEPSEEK_API_KEY='$DSK' setsid nohup bash /workspace/pod_eval_only.sh >/workspace/logs/eval_only.log 2>&1 </dev/null & echo STARTED" || { log "FATAL launch"; exit 1; }

log "polling (cap ~1.5h)…"; RC=""
for i in $(seq 1 45); do
  sleep 120
  OUT=$(TSSH -p "$PORT" root@"$HOST" 'printf "RC=%s\n" "$(cat /workspace/eval/_cycle_status 2>/dev/null)"; tail -1 /workspace/logs/eval_only.log 2>/dev/null' 2>/dev/null | tr -d '\r')
  RC=$(printf '%s\n' "$OUT" | sed -n 's/^RC=//p' | tr -d ' ')
  log "[$i/45] $(printf '%s\n' "$OUT" | grep -v '^RC=' | tail -1)"
  [ -n "$RC" ] && { log "finished (exit $RC)"; break; }
done

mkdir -p data/eval_reports
RSCP_PULL(){ timeout 90 scp -i "$KEYF" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":"$1" "$2" 2>/dev/null; }
RSCP_PULL /workspace/eval/aria_llm_v0_7_eval_rerun.json data/eval_reports/aria_llm_v0_7_eval_rerun.json || log "(report not pulled)"
log "=== RESULT ==="
python - <<'PY'
import json, os
f="data/eval_reports/aria_llm_v0_7_eval_rerun.json"
if not os.path.exists(f): print("report MISSING"); raise SystemExit
d=json.load(open(f,encoding="utf-8")); dd=d.get("defence_dd") or {}; pi=d.get("prompt_injection") or {}
a=dd.get("accuracy")
print(f"v0.7 (rerun, open-book): judge-DD={a} (n={dd.get('total')}) | leak={pi.get('leak_rate')} | DeepSeek=0.336 | G1 {'PASS' if (a or 0)>=0.336 else 'FAIL'}")
PY
stop_pod
log "DONE — pod $POD_ID stopped (volume + adapter preserved)."
