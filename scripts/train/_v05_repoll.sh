#!/usr/bin/env bash
# R-F2502 — reconnect poller for the v0.5 cycle after the original driver was
# killed. The cycle runs DETACHED on pod r4mzxe4i74c7nj (self-stop armed, so no
# burn). This ONLY polls for completion and pulls the EPHEMERAL eval report +
# log before the pod self-stops (vol=None -> /workspace is lost on stop).
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
POD=r4mzxe4i74c7nj
API="https://rest.runpod.io/v1"
API_KEY=$(grep -E "^RUNPOD_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
KEY=/tmp/rpkey_v05; cp ~/.ssh/runpod_aria "$KEY"; chmod 600 "$KEY"
SSH="ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=10"
jget(){ python -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1','') or '')" 2>/dev/null; }
pmget(){ python -c "import sys,json;d=json.load(sys.stdin);pm=d.get('portMappings') or {};print(pm.get('22') or '')" 2>/dev/null; }

PD=$(curl -s "$API/pods/$POD" -H "Authorization: Bearer $API_KEY")
HOST=$(echo "$PD" | jget publicIp); PORT=$(echo "$PD" | pmget)
echo "[repoll] pod $POD at $HOST:$PORT"
[ -n "$HOST" ] && [ -n "$PORT" ] || { echo "[repoll] FATAL: no endpoint"; exit 1; }

RC=""
for i in $(seq 1 150); do   # ~5h cap at 120s
  sleep 120
  OUT=$($SSH -p "$PORT" root@"$HOST" 'printf "RC=%s\n" "$(cat /workspace/eval/_cycle_status 2>/dev/null)"; tail -1 /workspace/logs/grounded_dpo_cycle.log 2>/dev/null' 2>/dev/null | tr -d '\r')
  RC=$(printf '%s\n' "$OUT" | sed -n 's/^RC=//p' | tr -d ' ')
  LINE=$(printf '%s\n' "$OUT" | grep -v '^RC=' | tail -1)
  echo "[repoll] [$i/150] ${LINE:-(no log line yet)}"
  [ -n "$RC" ] && { echo "[repoll] cycle finished (exit $RC)"; break; }
  ST=$(curl -s "$API/pods/$POD" -H "Authorization: Bearer $API_KEY" | jget desiredStatus)
  [ "$ST" = "EXITED" ] && { echo "[repoll] pod EXITED before RC caught — pulling whatever exists"; break; }
done

mkdir -p data/eval_reports
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/aria_llm_v0_4_dpo_eval.json data/eval_reports/aria_llm_v05_dpo_eval.json 2>/dev/null && echo "[repoll] EVAL REPORT pulled -> data/eval_reports/aria_llm_v05_dpo_eval.json" || echo "[repoll] WARN: eval report not pulled (pod may have stopped)"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/logs/grounded_dpo_cycle.log data/eval_reports/_v05_cycle_run.log 2>/dev/null && echo "[repoll] cycle log pulled" || echo "[repoll] (cycle log not pulled)"
# also try to pull the eval detail log (PI n=155 numbers)
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":'/workspace/logs/eval_*.log' data/eval_reports/ 2>/dev/null && echo "[repoll] eval detail logs pulled" || true
echo "[repoll] DONE (exit code from cycle: ${RC:-unknown})"
