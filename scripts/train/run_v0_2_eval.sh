#!/bin/bash
# R-F1474 — local orchestrator for the v0.2 500-Q comparison eval.
#
# Closes the v0.3-vs-v0.2 head-to-head: v0.3 already scored 0.22/500 on the
# validated judge; this evals the PERSISTED v0.2 DPO adapter on the SAME 500-Q so
# we get the apples-to-apples PROMOTE/KEEP verdict. NO training.
#
# Capacity-aware + console-friendly: the pinned host often refuses an API start
# ("not enough free GPUs"), but the operator can console-start the pod. This driver
# POLLS for the pod to become RUNNING (whether it started it via API or the operator
# via console), then proceeds. Fork-safe poll loop (R-F1473): ONE ssh per tick.
#
# Usage:  bash scripts/train/run_v0_2_eval.sh        (waits up to ~40min for RUNNING)
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
POD="${RUNPOD_POD_ID:-7ei3hldcpz4j2v}"
API="https://rest.runpod.io/v1"
API_KEY=$(grep -E "^RUNPOD_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
DSK=$(grep -E "^DEEPSEEK_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
[ -n "$API_KEY" ] || { echo "[v0.2] FATAL: RUNPOD_API_KEY not in .env"; exit 1; }
[ -n "$DSK" ] || echo "[v0.2] WARN: DEEPSEEK_API_KEY not in .env — judge will be skipped"

EVAL_LOCAL="data/eval_reports/aria_eval_500q.jsonl"
[ -s "$EVAL_LOCAL" ] || { echo "[v0.2] FATAL: eval set missing: $EVAL_LOCAL"; exit 1; }

KEY="/tmp/rpkey"; cp ~/.ssh/runpod_aria "$KEY"; chmod 600 "$KEY"
SSH="ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=10"
jget(){ python -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1','') or '')" 2>/dev/null; }
pmget(){ python -c "import sys,json;d=json.load(sys.stdin);pm=d.get('portMappings') or {};print(pm.get('22') or '')" 2>/dev/null; }
errget(){ python -c "import sys,json
try: d=json.load(sys.stdin); print(d.get('error','') if isinstance(d,dict) else '')
except: print('')" 2>/dev/null; }

stop_pod(){ echo "[v0.2] stopping pod $POD"; curl -s -X POST "$API/pods/$POD/stop" -H "Authorization: Bearer $API_KEY" >/dev/null 2>&1 || true; }
trap stop_pod EXIT

# 1. Wait for RUNNING — try an API start each round; if capacity-blocked, keep
#    polling (the operator may console-start it). ~40 attempts * 60s = ~40 min.
echo "[v0.2] waiting for pod $POD to be RUNNING (API-start each round; console-start also works)…"
HOST=""; PORT=""
for i in $(seq 1 40); do
  PD=$(curl -s "$API/pods/$POD" -H "Authorization: Bearer $API_KEY")
  ST=$(echo "$PD" | jget desiredStatus); HOST=$(echo "$PD" | jget publicIp); PORT=$(echo "$PD" | pmget)
  if [ "$ST" = "RUNNING" ] && [ -n "$HOST" ] && [ -n "$PORT" ]; then
    echo "[v0.2] pod RUNNING $HOST:$PORT"; break
  fi
  if [ "$ST" != "RUNNING" ]; then
    R=$(curl -s -X POST "$API/pods/$POD/start" -H "Authorization: Bearer $API_KEY")
    ERR=$(echo "$R" | errget)
    [ -n "$ERR" ] && echo "[v0.2] [$i/40] API start blocked ($ERR) — waiting for capacity / console-start…"
  fi
  HOST=""; PORT=""; sleep 60
done
[ -n "$HOST" ] && [ -n "$PORT" ] || { echo "[v0.2] FATAL: pod never reached RUNNING (capacity). Console-start it and re-run."; exit 1; }

# 2. Wait SSH
for i in $(seq 1 24); do $SSH -p "$PORT" root@"$HOST" "echo ok" 2>/dev/null | grep -q ok && { echo "[v0.2] SSH ready"; break; }; sleep 5; done

# 3. Push driver + current scripts + eval set (container was wiped on restart)
echo "[v0.2] pushing driver + scripts + eval set…"
$SSH -p "$PORT" root@"$HOST" "mkdir -p /workspace/datasets /workspace/crucix/scripts/train"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" scripts/train/v0_2_eval_pod_run.sh root@"$HOST":/workspace/ || { echo "[v0.2] FATAL scp driver"; exit 1; }
for f in serve_eval_shim.py eval_aria_llm.py; do
  scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "scripts/train/$f" root@"$HOST":/workspace/crucix/scripts/train/"$f" || { echo "[v0.2] FATAL scp $f"; exit 1; }
done
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$EVAL_LOCAL" root@"$HOST":/workspace/datasets/aria_eval_500q.jsonl || { echo "[v0.2] FATAL scp eval set"; exit 1; }

# 4. Launch DETACHED, then fork-safe poll (R-F1473: ONE ssh per tick)
echo "[v0.2] launching v0.2 eval DETACHED on the pod…"
$SSH -p "$PORT" root@"$HOST" \
  "rm -f /workspace/eval/_v0_2_status; mkdir -p /workspace/logs; \
   EVAL_SET=/workspace/datasets/aria_eval_500q.jsonl DEEPSEEK_API_KEY='$DSK' \
   setsid nohup bash /workspace/v0_2_eval_pod_run.sh > /workspace/logs/v0_2_eval.log 2>&1 < /dev/null & echo STARTED" \
  || { echo "[v0.2] FATAL: could not launch eval on pod"; exit 1; }

echo "[v0.2] polling for completion (cap ~8h; breaks as soon as it finishes)…"
# R-F1488: cap raised 4h -> 8h. The 4h cap KILLED a healthy 76%-done 500-Q eval
# on 2026-06-10 (the slow single-GPU shim needs ~5h). A real HANG is caught by the
# burn-guard (>15min idle), so the driver can afford a generous cap and never kill a
# slow-but-progressing run. The eval also checkpoints now (R-F1488), so even a kill
# resumes on re-run instead of losing work.
RC=""
for i in $(seq 1 240); do   # 240 * 120s = 8h cap
  sleep 120
  OUT=$($SSH -p "$PORT" root@"$HOST" \
    'printf "RC=%s\n" "$(cat /workspace/eval/_v0_2_status 2>/dev/null)"; tail -1 /workspace/logs/v0_2_eval.log 2>/dev/null' \
    2>/dev/null | tr -d '\r')
  RC=$(printf '%s\n' "$OUT" | sed -n 's/^RC=//p' | tr -d ' ')
  LINE=$(printf '%s\n' "$OUT" | grep -v '^RC=' | tail -1)
  echo "[v0.2] [$i/120] ${LINE:-(no log line yet)}"
  [ -n "$RC" ] && { echo "[v0.2] eval finished (exit code $RC)"; break; }
done
[ -n "$RC" ] || echo "[v0.2] WARN: no completion signal within the cap — pulling whatever exists, then stopping"

# 5. Pull v0.2 report
mkdir -p data/eval_reports
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/aria_llm_v0_2_eval.json data/eval_reports/aria_llm_v0_2_eval_500q.json 2>/dev/null && echo "[v0.2] pulled v0.2 report" || echo "[v0.2] (v0.2 report not pulled)"

# 6. Head-to-head verdict
echo "[v0.2] === v0.3 vs v0.2 HEAD-TO-HEAD (500-Q, validated judge) ==="
python - <<'PY'
import json, os
def dd(r): return ((r or {}).get("defence_dd") or (r or {}).get("dd_eval") or {})
def pi(r): return ((r or {}).get("prompt_injection") or {})
def load(f):
    if not os.path.exists(f): return None
    try: return json.load(open(f, encoding="utf-8"))
    except Exception: return None
v3 = load("data/eval_reports/aria_llm_v0_3_eval.json")
v2 = load("data/eval_reports/aria_llm_v0_2_eval_500q.json")
a3, a2 = dd(v3).get("accuracy"), dd(v2).get("accuracy")
l3, l2 = pi(v3).get("leak_rate"), pi(v2).get("leak_rate")
print(f"v0.3 judge-DD: {a3} (n={dd(v3).get('total')}) | leak_rate={l3}")
print(f"v0.2 judge-DD: {a2} (n={dd(v2).get('total')}) | leak_rate={l2}")
if a3 is None or a2 is None:
    print("VERDICT: INCOMPLETE — missing a report.")
else:
    acc_ok = a3 >= a2
    leak_ok = (l3 is None or l2 is None) or (l3 <= l2)
    if acc_ok and leak_ok:
        print(f"VERDICT: PROMOTE v0.3 ✅ (acc {a3} >= {a2}; leak ok). First distillation cycle moved the number.")
    else:
        why = []
        if not acc_ok: why.append(f"acc {a3} < {a2}")
        if not leak_ok: why.append(f"leak {l3} > {l2}")
        print(f"VERDICT: KEEP v0.2 — v0.3 did not clear the bar ({'; '.join(why)}).")
PY
stop_pod
echo "[v0.2] DONE — pod stopped."
