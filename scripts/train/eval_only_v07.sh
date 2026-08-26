#!/bin/bash
# EVAL-ONLY v0.7 driver (R-F17xx) — operator approved an OFFLINE eval of the best
# own-model. Serves the EXISTING on-volume adapter (NO retrain) and runs the 500-Q
# open-book eval, then STOPS the pod. Hardened like run_v0_7_grounded_cycle.sh:
# auto-arms the on-pod self-stop watcher + timeout-wraps every poll SSH, so a hung
# Windows SSH can't freeze the loop and the pod self-stops even if this driver dies.
# Does NOT set ARIA_LLM_URL. Does NOT retrain.
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
POD="${RUNPOD_POD_ID:-2adkzeri6fa2zi}"
API="https://rest.runpod.io/v1"
API_KEY=$(grep -E "^RUNPOD_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
DSK=$(grep -E "^DEEPSEEK_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
[ -n "$API_KEY" ] || { echo "[eval] FATAL: RUNPOD_API_KEY not in .env"; exit 1; }
EVAL_LOCAL="${EVAL_LOCAL:-data/eval_reports/aria_eval_500q_openbook.jsonl}"
[ -s "$EVAL_LOCAL" ] || EVAL_LOCAL="data/training/aria_eval_500q_openbook.jsonl"
[ -s "$EVAL_LOCAL" ] || { echo "[eval] FATAL: open-book eval set not found"; exit 1; }
echo "[eval] open-book eval set: $EVAL_LOCAL ($(wc -l < "$EVAL_LOCAL") Q)"

KEY="/tmp/rpkey_eval"; cp ~/.ssh/runpod_aria "$KEY"; chmod 600 "$KEY"
SSH="ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=6"
TSSH(){ timeout 75 $SSH "$@"; }
jget(){ python -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1','') or '')" 2>/dev/null; }
pmget(){ python -c "import sys,json;d=json.load(sys.stdin);pm=d.get('portMappings') or {};print(pm.get('22') or '')" 2>/dev/null; }
stop_pod(){ echo "[eval] stopping pod $POD"; curl -s -X POST "$API/pods/$POD/stop" -H "Authorization: Bearer $API_KEY" >/dev/null 2>&1 || true; }
trap stop_pod EXIT

# 1. Ensure RUNNING (US-KS-2 is capacity-blocked intermittently → retry up to ~40 min)
HOST=""; PORT=""
for i in $(seq 1 40); do
  PD=$(curl -s "$API/pods/$POD" -H "Authorization: Bearer $API_KEY")
  ST=$(echo "$PD" | jget desiredStatus)
  if [ "$ST" != "RUNNING" ]; then
    R=$(curl -s -X POST "$API/pods/$POD/start" -H "Authorization: Bearer $API_KEY")
    ERR=$(echo "$R" | python -c "import sys,json
try: d=json.load(sys.stdin); print(d.get('error','') if isinstance(d,dict) else '')
except: print('')" 2>/dev/null || echo "")
    [ -n "$ERR" ] && { echo "[eval] start blocked ($ERR) — retry 60s ($i/20)"; sleep 60; continue; }
  fi
  for j in $(seq 1 18); do
    PD=$(curl -s "$API/pods/$POD" -H "Authorization: Bearer $API_KEY")
    ST=$(echo "$PD" | jget desiredStatus); HOST=$(echo "$PD" | jget publicIp); PORT=$(echo "$PD" | pmget)
    [ "$ST" = "RUNNING" ] && [ -n "$HOST" ] && [ -n "$PORT" ] && break
    sleep 8
  done
  [ -n "$HOST" ] && [ -n "$PORT" ] && { echo "[eval] pod RUNNING $HOST:$PORT"; break; }
  sleep 30
done
[ -n "$HOST" ] && [ -n "$PORT" ] || { echo "[eval] FATAL: pod not RUNNING"; exit 1; }

# 2. Wait SSH, then settle (sshd is flaky in the first ~30s after boot → "Connection
#    closed by remote host" on scp). R-F17xx: wait for THREE consecutive good probes.
ok=0
for i in $(seq 1 36); do
  if TSSH -p "$PORT" root@"$HOST" "echo ok" 2>/dev/null | grep -q ok; then ok=$((ok+1)); else ok=0; fi
  [ "$ok" -ge 3 ] && { echo "[eval] SSH stable ($i)"; break; }
  sleep 5
done
sleep 15   # extra settle before bulk transfers

# scp with retry — flaky post-boot sshd drops the first connections.
RSCP(){ local src="$1" dst="$2" t; for t in 1 2 3 4 5; do
  if timeout 90 scp -i "$KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=15 -P "$PORT" "$src" root@"$HOST":"$dst" 2>/dev/null; then return 0; fi
  echo "[eval] scp retry $t for $(basename "$src")…"; sleep 10
done; return 1; }

# 3. Push runner + scripts + open-book eval set + self-stop watcher + eval deps
echo "[eval] pushing runner + scripts + eval set + watcher…"
for i in 1 2 3; do TSSH -p "$PORT" root@"$HOST" "mkdir -p /workspace/datasets /workspace/crucix/scripts/train /workspace/crucix/aria_service/intel /workspace/logs" 2>/dev/null && break; sleep 8; done
RSCP scripts/train/pod_eval_only.sh         /workspace/                                  || { echo "[eval] FATAL scp runner"; exit 1; }
RSCP scripts/train/pod_selfstop_watch_v04.sh /workspace/                                 || { echo "[eval] FATAL scp watcher"; exit 1; }
# R-F4350 (C-295) — pod_eval_only.sh now sources hf_cache_select.sh and fails
# CLOSED without it, so the helper must ship before it runs. Its loader searches
# /workspace/crucix/scripts/train second, which is where this loop lands.
for f in serve_eval_shim.py eval_aria_llm.py hf_cache_select.sh; do
  RSCP "scripts/train/$f" "/workspace/crucix/scripts/train/$f" || { echo "[eval] FATAL scp $f"; exit 1; }
done
RSCP "$EVAL_LOCAL" /workspace/datasets/aria_eval_openbook.jsonl || { echo "[eval] FATAL scp eval set"; exit 1; }
for f in aria_service/__init__.py aria_service/intel/__init__.py aria_service/intel/engine_wiring.py aria_service/intel/prompt_injection_suite.py; do
  RSCP "$f" "/workspace/crucix/$f" || echo "[eval] (optional dep $f not pushed)"
done

# 4. ARM the self-stop watcher FIRST (cost safety even if this driver dies), then launch eval-only DETACHED
echo "[eval] ARMING on-pod self-stop watcher (GRACE=900)…"
TSSH -p "$PORT" root@"$HOST" \
  "POD_ID=$POD RP_KEY='$API_KEY' GRACE=900 setsid nohup bash /workspace/pod_selfstop_watch_v04.sh >/workspace/logs/_selfstop_launch.log 2>&1 < /dev/null & echo ARMED" \
  || echo "[eval] WARN: watcher arm failed — relying on EXIT trap"
echo "[eval] launching EVAL-ONLY runner DETACHED…"
TSSH -p "$PORT" root@"$HOST" \
  "rm -f /workspace/eval/_cycle_status; DEEPSEEK_API_KEY='$DSK' \
   setsid nohup bash /workspace/pod_eval_only.sh > /workspace/logs/eval_only.log 2>&1 < /dev/null & echo STARTED" \
  || { echo "[eval] FATAL: could not launch eval runner"; exit 1; }

# 5. Poll for completion (cap ~1.5h; timeout-wrapped SSH)
echo "[eval] polling (cap ~1.5h)…"
RC=""
for i in $(seq 1 45); do
  sleep 120
  OUT=$(TSSH -p "$PORT" root@"$HOST" \
    'printf "RC=%s\n" "$(cat /workspace/eval/_cycle_status 2>/dev/null)"; tail -1 /workspace/logs/eval_only.log 2>/dev/null' \
    2>/dev/null | tr -d '\r')
  RC=$(printf '%s\n' "$OUT" | sed -n 's/^RC=//p' | tr -d ' ')
  LINE=$(printf '%s\n' "$OUT" | grep -v '^RC=' | tail -1)
  echo "[eval] [$i/45] ${LINE:-(no log line yet)}"
  [ -n "$RC" ] && { echo "[eval] runner finished (exit $RC)"; break; }
done

# 6. Pull report
mkdir -p data/eval_reports
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/aria_llm_v0_7_eval_rerun.json data/eval_reports/aria_llm_v0_7_eval_rerun.json 2>/dev/null || echo "[eval] (report not pulled)"
echo "[eval] === RESULT ==="
python - <<'PY'
import json, os
f="data/eval_reports/aria_llm_v0_7_eval_rerun.json"
if not os.path.exists(f): print("report MISSING — check pod logs"); raise SystemExit
d=json.load(open(f,encoding="utf-8")); dd=d.get("defence_dd") or {}; pi=d.get("prompt_injection") or {}
a=dd.get("accuracy")
print(f"v0.7 (rerun, open-book): judge-DD={a} (n={dd.get('total')}) | leak_rate={pi.get('leak_rate')}")
print(f"DeepSeek parity=0.336 | prior v0.7=0.308 | G1: {'PASS' if (a or 0)>=0.336 else 'FAIL'}")
PY
stop_pod
echo "[eval] DONE — pod stopped."
