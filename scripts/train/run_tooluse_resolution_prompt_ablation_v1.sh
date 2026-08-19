#!/usr/bin/env bash
# R-F4153 — bounded, evaluation-only prompt-policy ablation on the accepted parent.
set -uo pipefail

DRIVER_SOURCE="${ARIA_DRIVER_SOURCE:-${BASH_SOURCE[0]}}"
SCRIPT_DIR=$(cd "$(dirname "$DRIVER_SOURCE")" && pwd) || exit 1
REPO="${REPO:-$(cd "$SCRIPT_DIR/../.." && pwd)}"; cd "$REPO" || exit 1
API=https://rest.runpod.io/v1
PYBIN="${PYBIN:-.venv/Scripts/python.exe}"
ADAPTER=data/training/checkpoints/aria_tooluse_curve_sft_v5.tgz
EVAL=data/training/split_v1/eval.jsonl
POLICY=data/training/resolution_prompt_policy_v1.txt
MANIFEST=data/eval_reports/aria_tooluse_resolution_prompt_ablation_v1_manifest.json
BASELINE_OUT=data/eval_reports/aria_tooluse_resolution_prompt_ablation_v1_baseline.json
POLICY_OUT=data/eval_reports/aria_tooluse_resolution_prompt_ablation_v1_policy.json
STATE_FILE=data/eval_reports/.tooluse_resolution_prompt_ablation_v1_pod_state
UPLOAD_DEADLINE=5400
CYCLE_DEADLINE=10800
GRACE=900
COLLECT_GRACE=900
MAX_CREATE_TRIES=15
CREATE_RETRY_SECS=90

log(){ echo "[$(date -u +%H:%M:%S)] [prompt-ablation] $*"; }
jget(){ "$PYBIN" -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1','') or '')" 2>/dev/null; }
pmget(){ "$PYBIN" -c "import sys,json;d=json.load(sys.stdin);print((d.get('portMappings') or {}).get('22') or '')" 2>/dev/null; }
sha(){ sha256sum "$1" | awk '{print $1}'; }

for file in "$ADAPTER" "$EVAL" "$POLICY" "$MANIFEST"; do
  [ -s "$file" ] || { log "FATAL missing $file"; exit 1; }
done
"$PYBIN" - "$MANIFEST" "$ADAPTER" "$EVAL" "$POLICY" <<'PY' || exit 3
import hashlib, json, sys
from pathlib import Path
manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for key, path in (("parent_adapter_sha256", sys.argv[2]),
                  ("eval_sha256", sys.argv[3]), ("policy_sha256", sys.argv[4])):
    actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if actual != manifest[key]:
        raise SystemExit(f"{key} mismatch: {actual}")
if manifest.get("weights_mutated") is not False:
    raise SystemExit("experiment is not evaluation-only")
if manifest.get("promotion_authorized") is not False:
    raise SystemExit("experiment unexpectedly authorizes promotion")
print("verified hash-pinned, evaluation-only manifest")
PY
tar -tzf "$ADAPTER" | awk '/\/adapter_config.json$/ { found=1 } END { exit !found }' \
  || { log "FATAL adapter archive invalid"; exit 1; }
ADAPTER_DIR=$(tar -tzf "$ADAPTER" | awk -F/ '/\/adapter_config.json$/ { print $1; exit }')
case "$ADAPTER_DIR" in ""|*/*|*".."*) log "FATAL unsafe adapter directory"; exit 1;; esac
EXPECTED_ROWS=$("$PYBIN" -c "import json;print(json.load(open('$MANIFEST'))['expected_rows'])")
POLICY_SHA=$(sha "$POLICY")
EFFECTIVE_POLICY_SHA=$("$PYBIN" -c "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('$POLICY').read_text(encoding='utf-8').strip().encode('utf-8')).hexdigest())")
ADAPTER_SHA=$(sha "$ADAPTER")
EVAL_SHA=$(sha "$EVAL")
KEY=$(grep -E '^RUNPOD_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
[ -n "$KEY" ] || { log "FATAL API key unavailable"; exit 1; }

POD_ID=""; HOST=""; PORT=""; PREARM_PID=""
pod_state(){
  local body state
  body=$(curl.exe -fsS --connect-timeout 10 --max-time 20 "$API/pods/$POD_ID" \
    -H "Authorization: Bearer $KEY" 2>/dev/null) || { echo UNREADABLE; return; }
  state=$(printf '%s' "$body" | jget desiredStatus)
  case "$state" in RUNNING|CREATED|STARTING|RESTARTING) echo RUNNING;;
    EXITED|STOPPED|TERMINATED) echo NOT_RUNNING;; *) echo UNREADABLE;; esac
}
release(){
  [ -z "$PREARM_PID" ] || { kill "$PREARM_PID" 2>/dev/null || true; wait "$PREARM_PID" 2>/dev/null || true; PREARM_PID=""; }
  [ -n "$POD_ID" ] || return 0
  log "stopping pod $POD_ID"
  for attempt in 1 2 3; do
    curl.exe -s -X POST "$API/pods/$POD_ID/stop" -H "Authorization: Bearer $KEY" >/dev/null 2>&1 || true
    [ "$(pod_state)" = NOT_RUNNING ] && { log "verified pod stopped"; return 0; }
    log "stop unverified attempt $attempt/3"; sleep 10
  done
  log "FATAL pod stop unverified"; return 1
}
trap release EXIT

for try in $(seq 1 "$MAX_CREATE_TRIES"); do
  POD_ID=$("$PYBIN" scripts/train/_create_v04_pod.py 2>/dev/null | head -1 | tr -d '[:space:]')
  [ -n "$POD_ID" ] || { log "create rejected $try/$MAX_CREATE_TRIES"; sleep "$CREATE_RETRY_SECS"; continue; }
  log "created $POD_ID; waiting for RUNNING"
  for _ in $(seq 1 40); do
    BODY=$(curl.exe -s "$API/pods/$POD_ID" -H "Authorization: Bearer $KEY")
    STATUS=$(printf '%s' "$BODY" | jget desiredStatus)
    HOST=$(printf '%s' "$BODY" | jget publicIp); PORT=$(printf '%s' "$BODY" | pmget)
    [ "$STATUS" = RUNNING ] && [ -n "$HOST" ] && [ -n "$PORT" ] && break
    sleep 10
  done
  [ -n "$HOST" ] && [ -n "$PORT" ] && break
  release; POD_ID=""; sleep "$CREATE_RETRY_SECS"
done
[ -n "$POD_ID" ] && [ -n "$HOST" ] && [ -n "$PORT" ] || { log "BLOCKED no GPU capacity"; exit 2; }
mkdir -p "$(dirname "$STATE_FILE")"
printf 'POD_ID=%s\nHOST=%s\nPORT=%s\nPHASE=starting\n' "$POD_ID" "$HOST" "$PORT" > "$STATE_FILE"
(
  sleep 900
  curl.exe -s -X POST "$API/pods/$POD_ID/stop" -H "Authorization: Bearer $KEY" >/dev/null 2>&1 || true
) & PREARM_PID=$!
log "host pre-arm watchdog armed"

KEYF=/tmp/rpkey_prompt_ablation; cp ~/.ssh/runpod_aria "$KEYF"; chmod 600 "$KEYF"
SSH_KEYS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
SSH="ssh -i $KEYF $SSH_KEYS -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=6"
TSSH(){ timeout 75 $SSH "$@"; }
ok=0
for _ in $(seq 1 40); do
  if TSSH -p "$PORT" root@"$HOST" 'echo ok' 2>/dev/null | grep -q ok; then ok=$((ok+1)); else ok=0; fi
  [ "$ok" -ge 3 ] && break; sleep 5
done
[ "$ok" -ge 3 ] || { log "FATAL SSH unstable"; exit 1; }
TSSH -p "$PORT" root@"$HOST" 'mkdir -p /workspace/checkpoints /workspace/datasets /workspace/eval /workspace/logs /workspace/crucix/scripts/train' || exit 1
RSCP(){ timeout 180 scp -i "$KEYF" $SSH_KEYS -o ConnectTimeout=15 -P "$PORT" "$1" root@"$HOST":"$2" 2>/dev/null; }
for item in \
  "scripts/train/pod_tooluse_prompt_ablation.sh:/workspace/pod_tooluse_prompt_ablation.sh" \
  "scripts/train/pod_selfstop_watch_v04.sh:/workspace/pod_selfstop_watch_v04.sh" \
  "scripts/train/eval_tooluse.py:/workspace/crucix/scripts/train/eval_tooluse.py" \
  "scripts/train/serve_eval_shim.py:/workspace/crucix/scripts/train/serve_eval_shim.py" \
  "scripts/train/build_tooluse_corpus.py:/workspace/crucix/scripts/train/build_tooluse_corpus.py" \
  "$EVAL:/workspace/datasets/aria_tooluse_eval.jsonl" \
  "$POLICY:/workspace/datasets/resolution_prompt_policy_v1.txt"; do
  src=${item%%:*}; dst=${item#*:}; RSCP "$src" "$dst" || { log "FATAL upload $src"; exit 1; }
done
arm_watchdog(){
  local deadline=$1 log_name=$2
  TSSH -p "$PORT" root@"$HOST" \
    "if [ -s /workspace/eval/_watchdog_pid ]; then kill \$(cat /workspace/eval/_watchdog_pid) 2>/dev/null || true; fi; POD_ID=$POD_ID RP_KEY='$KEY' DEADLINE=$deadline GRACE=$GRACE COLLECT_GRACE=$COLLECT_GRACE setsid nohup bash /workspace/pod_selfstop_watch_v04.sh >/workspace/logs/$log_name 2>&1 </dev/null & echo \$! >/workspace/eval/_watchdog_pid" >/dev/null \
    && TSSH -p "$PORT" root@"$HOST" 'kill -0 "$(cat /workspace/eval/_watchdog_pid)"' >/dev/null
}
arm_watchdog "$UPLOAD_DEADLINE" _prompt_upload_watch.log || { log "FATAL upload watchdog"; exit 1; }
kill "$PREARM_PID" 2>/dev/null || true; wait "$PREARM_PID" 2>/dev/null || true; PREARM_PID=""

log "uploading accepted adapter with resumable SFTP"
UPLOAD_OK=0
for slice in $(seq 1 7); do
  if TSSH -p "$PORT" root@"$HOST" 'test -f /workspace/accepted_parent.tgz' >/dev/null 2>&1; then mode=reput; else mode=put; fi
  printf '%s %s %s\n' "$mode" "$ADAPTER" /workspace/accepted_parent.tgz \
    | timeout 720 sftp -b - -i "$KEYF" $SSH_KEYS -o ConnectTimeout=20 -P "$PORT" root@"$HOST" >/dev/null \
    && { UPLOAD_OK=1; break; }
  log "adapter slice $slice/7 incomplete state=$(pod_state)"
done
[ "$UPLOAD_OK" = 1 ] || { log "FATAL bounded adapter upload incomplete"; exit 1; }
TSSH -p "$PORT" root@"$HOST" \
  "printf '%s  %s\n%s  %s\n%s  %s\n' '$ADAPTER_SHA' /workspace/accepted_parent.tgz '$EVAL_SHA' /workspace/datasets/aria_tooluse_eval.jsonl '$POLICY_SHA' /workspace/datasets/resolution_prompt_policy_v1.txt | sha256sum -c - && tar -xzf /workspace/accepted_parent.tgz -C /workspace/checkpoints" \
  || { log "FATAL remote immutable input validation"; exit 1; }

arm_watchdog "$CYCLE_DEADLINE" _prompt_cycle_watch.log || { log "FATAL cycle watchdog"; exit 1; }
printf 'POD_ID=%s\nHOST=%s\nPORT=%s\nPHASE=evaluating\n' "$POD_ID" "$HOST" "$PORT" > "$STATE_FILE"
TSSH -p "$PORT" root@"$HOST" \
  "ADAPTER='/workspace/checkpoints/$ADAPTER_DIR' EXPECTED_ROWS=$EXPECTED_ROWS EXPECTED_POLICY_SHA256='$POLICY_SHA' EXPECTED_EFFECTIVE_POLICY_SHA256='$EFFECTIVE_POLICY_SHA' setsid nohup bash /workspace/pod_tooluse_prompt_ablation.sh >/workspace/logs/prompt_ablation_cycle.log 2>&1 </dev/null & echo STARTED" \
  | grep -q STARTED || { log "FATAL cycle start"; exit 1; }

log "paired evaluation started"
RC=""
for tick in $(seq 1 120); do
  RC=$(TSSH -p "$PORT" root@"$HOST" 'cat /workspace/eval/_cycle_status 2>/dev/null' 2>/dev/null | tr -d '\r[:space:]')
  [ -n "$RC" ] && break
  [ $((tick % 5)) -ne 0 ] || log "still evaluating tick=$tick state=$(pod_state)"
  sleep 90
done
[ "$RC" = 0 ] || { log "FATAL cycle rc=${RC:-missing}"; exit 1; }
RSCP_PULL(){ timeout 600 scp -i "$KEYF" $SSH_KEYS -P "$PORT" root@"$HOST":"$1" "$2" 2>/dev/null; }
RSCP_PULL /workspace/eval/aria_tooluse_resolution_prompt_ablation_v1_baseline.json "$BASELINE_OUT" || exit 1
RSCP_PULL /workspace/eval/aria_tooluse_resolution_prompt_ablation_v1_policy.json "$POLICY_OUT" || exit 1
"$PYBIN" - "$MANIFEST" "$BASELINE_OUT" "$POLICY_OUT" <<'PY' || exit 1
import json, sys
manifest, baseline, policy = [json.load(open(path, encoding="utf-8")) for path in sys.argv[1:]]
expected = manifest["expected_rows"]
for name, report in (("baseline", baseline), ("policy", policy)):
    if report.get("complete") is not True or report.get("total") != expected or len(report.get("rows") or []) != expected:
        raise SystemExit(f"{name} report incomplete")
def axes(report): return {a["label"]: a["honest"] for a in report["per_axis"]}
b, p = axes(baseline), axes(policy)
registered = manifest["baseline"]
if (baseline["honest"] != registered["honest"] or
        b.get("tooluse_resolution") != registered["resolution_honest"] or
        next(a["total"] for a in baseline["per_axis"]
             if a["label"] == "tooluse_resolution") != registered["resolution_total"]):
    raise SystemExit("baseline arm does not reproduce the registered parent; ablation invalid")
regressions = sorted(axis for axis in b if p.get(axis, -1) < b[axis])
gate = manifest["success_gate"]
passed = (policy["honest"] >= gate["minimum_honest"] and
          p.get("tooluse_resolution", 0) >= gate["minimum_resolution_honest"] and
          len(regressions) <= gate["maximum_axis_regressions"])
print(json.dumps({"baseline": baseline["honest"], "policy": policy["honest"],
                  "resolution": p.get("tooluse_resolution"), "regressions": regressions,
                  "ablation_pass": passed, "promotion_authorized": False}, indent=2))
PY
printf 'POD_ID=%s\nHOST=%s\nPORT=%s\nPHASE=complete\n' "$POD_ID" "$HOST" "$PORT" > "$STATE_FILE"
log "DONE paired reports harvested; no promotion path exists"
