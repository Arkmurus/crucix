#!/usr/bin/env bash
# R-F3815 — bounded v2-to-v3 DPO continuation and held-out promotion evaluation.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd) || exit 1
REPO="${REPO:-$(cd "$SCRIPT_DIR/../.." && pwd)}"; cd "$REPO" || exit 1
API=https://rest.runpod.io/v1
PYBIN="${PYBIN:-.venv/Scripts/python.exe}"
FRESH_BASE="${FRESH_BASE:-0}"
EXPECTED_DPO_PAIRS="${EXPECTED_DPO_PAIRS:-8}"
ADAPTER_LOCAL="${ADAPTER_LOCAL:-data/training/checkpoints/aria_tooluse_dpo_v2.tgz}"
RESUME_ADAPTER_LOCAL="${RESUME_ADAPTER_LOCAL:-}"
RESUME_REPORT_LOCAL="${RESUME_REPORT_LOCAL:-}"
DPO_LOCAL="${DPO_LOCAL:-data/training/aria_tooluse_dpo_v3.jsonl}"
EVAL_LOCAL="${EVAL_LOCAL:-data/training/split_v2/eval.jsonl}"
TRAIN_PROOF="${TRAIN_PROOF:-data/training/tooluse_dpo_generation_v3.jsonl}"
GOLDEN="${GOLDEN:-data/eval_frozen/aria_eval_500q.jsonl}"
REPORT_LOCAL="${REPORT_LOCAL:-data/eval_reports/aria_tooluse_dpo_v3_eval.json}"
OUTPUT_LOCAL="${OUTPUT_LOCAL:-data/training/checkpoints/aria_tooluse_dpo_v3.tgz}"
STATE_FILE="${STATE_FILE:-data/eval_reports/.tooluse_dpo_v3_pod_state}"
REMOTE_DPO_OUT="${REMOTE_DPO_OUT:-/workspace/checkpoints/aria_tooluse_dpo_v3}"
ADAPTER_SHA256="${ADAPTER_SHA256:-0fd0b88b16a47bc9276bc1dc96b90a488dad810b8bf296a00147b8fe989f1656}"
DPO_SHA256="${DPO_SHA256:-ef87c13d77e241ca295eb540ed64142e5c3669283b4f3913fa36923c05f5f991}"
EVAL_SHA256=d24be361fb30ff0e51272b2a7338be2924b8df5428d55a469f1c907bd28c3b00
# Separate measured envelopes: prior 311 MB upload <=64 min; 168-row eval ~74 min.
UPLOAD_DEADLINE="${UPLOAD_DEADLINE:-5400}"; CYCLE_DEADLINE="${CYCLE_DEADLINE:-7200}"
UPLOAD_SLICE="${UPLOAD_SLICE:-720}"; UPLOAD_SLICES="${UPLOAD_SLICES:-7}"
GRACE="${GRACE:-900}"; COLLECT_GRACE="${COLLECT_GRACE:-900}"
log(){ echo "[$(date -u +%H:%M:%S)] [tooluse-dpo] $*"; }
jget(){ "$PYBIN" -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1','') or '')" 2>/dev/null; }
pmget(){ "$PYBIN" -c "import sys,json;d=json.load(sys.stdin);print((d.get('portMappings') or {}).get('22') or '')" 2>/dev/null; }
pod_state(){
  local body state
  body=$(curl -fsS --connect-timeout 10 --max-time 20 "$API/pods/$POD_ID" -H "Authorization: Bearer $KEY" 2>/dev/null) || { echo UNREADABLE; return; }
  state=$(printf '%s' "$body" | jget desiredStatus) || { echo UNREADABLE; return; }
  case "$state" in RUNNING|CREATED|STARTING|RESTARTING) echo RUNNING;; EXITED|STOPPED|TERMINATED) echo NOT_RUNNING;; *) echo UNREADABLE;; esac
}
KEY=$(grep -E '^RUNPOD_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
[ -n "$KEY" ] || { log "FATAL API key unavailable"; exit 1; }
RESUME_MODE=0
if [ -n "$RESUME_ADAPTER_LOCAL" ] || [ -n "$RESUME_REPORT_LOCAL" ]; then
  [ -n "$RESUME_ADAPTER_LOCAL" ] && [ -n "$RESUME_REPORT_LOCAL" ] \
    || { log "FATAL resume adapter and report must be supplied together"; exit 1; }
  RESUME_MODE=1
fi
[ "$FRESH_BASE" != 1 ] || [ "$RESUME_MODE" = 0 ] \
  || { log "FATAL fresh-base mode cannot resume an adapter"; exit 1; }
UPLOAD_ADAPTER_LOCAL="$ADAPTER_LOCAL"
[ "$RESUME_MODE" = 0 ] || UPLOAD_ADAPTER_LOCAL="$RESUME_ADAPTER_LOCAL"
REQUIRED_FILES=("$DPO_LOCAL" "$EVAL_LOCAL" "$TRAIN_PROOF")
[ "$FRESH_BASE" = 1 ] || REQUIRED_FILES+=("$UPLOAD_ADAPTER_LOCAL")
for f in "${REQUIRED_FILES[@]}"; do [ -s "$f" ] || { log "FATAL missing $f"; exit 1; }; done
[ "$RESUME_MODE" = 0 ] || [ -s "$RESUME_REPORT_LOCAL" ] || { log "FATAL missing resume report"; exit 1; }
if [ "$RESUME_MODE" = 0 ]; then
  if [ "$FRESH_BASE" = 1 ]; then
    printf '%s  %s\n%s  %s\n' "$DPO_SHA256" "$DPO_LOCAL" "$EVAL_SHA256" "$EVAL_LOCAL" \
      | sha256sum -c - || { log "FATAL immutable fresh input hash mismatch"; exit 1; }
  else
    printf '%s  %s\n%s  %s\n%s  %s\n' "$ADAPTER_SHA256" "$ADAPTER_LOCAL" "$DPO_SHA256" "$DPO_LOCAL" "$EVAL_SHA256" "$EVAL_LOCAL" \
      | sha256sum -c - || { log "FATAL immutable input hash mismatch"; exit 1; }
  fi
else
  "$PYBIN" - "$RESUME_REPORT_LOCAL" <<'PY' || exit 3
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8"))
assert d.get("complete") is False
assert 0 < len(d.get("rows") or []) < 168
assert d.get("run", {}).get("total") == 168
print(f"verified resumable held-out prefix: {len(d['rows'])}/168")
PY
fi
if [ "$FRESH_BASE" != 1 ]; then
  ADAPTER_CONFIG_ENTRIES=$(tar -tzf "$UPLOAD_ADAPTER_LOCAL" | awk '/\/adapter_config.json$/ { print }') \
    || { log "FATAL unreadable SFT archive"; exit 1; }
  [ "$(printf '%s\n' "$ADAPTER_CONFIG_ENTRIES" | awk 'NF { n++ } END { print n+0 }')" = 1 ] \
    || { log "FATAL SFT archive must contain exactly one adapter"; exit 1; }
  ARCHIVE_ADAPTER_DIR=${ADAPTER_CONFIG_ENTRIES%/adapter_config.json}
  case "$ARCHIVE_ADAPTER_DIR" in
    ""|/*|..|../*|*/../*|*/..) log "FATAL unsafe SFT adapter archive path"; exit 1;;
  esac
  REMOTE_SFT_ADAPTER="/workspace/checkpoints/$ARCHIVE_ADAPTER_DIR"
fi
"$PYBIN" -m scripts.train.preflight_cycle --train-file "$TRAIN_PROOF" --eval-file "$EVAL_LOCAL" \
  --base-model mistralai/Mistral-7B-Instruct-v0.3 --golden-set "$GOLDEN" --strict || exit 3
"$PYBIN" - "$DPO_LOCAL" "$EXPECTED_DPO_PAIRS" <<'PY' || exit 3
import json, sys
r=[json.loads(x) for x in open(sys.argv[1], encoding="utf-8") if x.strip()]
expected=int(sys.argv[2])
assert len(r)==expected, f"expected {expected} pairs, got {len(r)}"
assert all(x.get("chosen") and x.get("rejected") and x["chosen"]!=x["rejected"] for x in r)
print(f"verified {expected} non-degenerate DPO pairs")
PY
POD_ID=""; HOST=""; PORT=""
release(){ [ -z "$POD_ID" ] || { log "stopping pod $POD_ID"; curl -s -X POST "$API/pods/$POD_ID/stop" -H "Authorization: Bearer $KEY" >/dev/null 2>&1; }; }
trap release EXIT
for i in $(seq 1 15); do
  POD_ID=$("$PYBIN" scripts/train/_create_v04_pod.py 2>/dev/null | head -1 | tr -d '[:space:]')
  [ -n "$POD_ID" ] || { log "create rejected $i/15"; sleep 90; continue; }
  for _ in $(seq 1 40); do
    PD=$(curl -s "$API/pods/$POD_ID" -H "Authorization: Bearer $KEY"); ST=$(printf '%s' "$PD" | jget desiredStatus)
    HOST=$(printf '%s' "$PD" | jget publicIp); PORT=$(printf '%s' "$PD" | pmget)
    [ "$ST" = RUNNING ] && [ -n "$HOST" ] && [ -n "$PORT" ] && break; sleep 10
  done
  [ -n "$HOST" ] && [ -n "$PORT" ] && break; release; POD_ID=""; sleep 90
done
[ -n "$POD_ID" ] && [ -n "$HOST" ] && [ -n "$PORT" ] || { log "BLOCKED no GPU capacity"; exit 2; }
KEYF=/tmp/rpkey_tooluse_dpo; cp ~/.ssh/runpod_aria "$KEYF"; chmod 600 "$KEYF"
SSH="ssh -i $KEYF -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=6"
TSSH(){ timeout 75 $SSH "$@"; }; ok=0
for _ in $(seq 1 40); do if TSSH -p "$PORT" root@"$HOST" 'echo ok' 2>/dev/null | grep -q ok; then ok=$((ok+1)); else ok=0; fi; [ "$ok" -ge 3 ] && break; sleep 5; done
[ "$ok" -ge 3 ] || { log "FATAL SSH unstable"; exit 1; }
TSSH -p "$PORT" root@"$HOST" 'mkdir -p /workspace/checkpoints /workspace/datasets /workspace/eval /workspace/logs /workspace/crucix/scripts/train' || exit 1
RSCP(){ timeout 180 scp -i "$KEYF" -o StrictHostKeyChecking=no -o ConnectTimeout=15 -P "$PORT" "$1" root@"$HOST":"$2" 2>/dev/null; }
for item in "scripts/train/pod_tooluse_dpo.sh:/workspace/pod_tooluse_dpo.sh" "scripts/train/pod_selfstop_watch_v04.sh:/workspace/pod_selfstop_watch_v04.sh" "scripts/train/dpo_train.py:/workspace/crucix/scripts/train/dpo_train.py" "scripts/train/eval_tooluse.py:/workspace/crucix/scripts/train/eval_tooluse.py" "scripts/train/build_tooluse_corpus.py:/workspace/crucix/scripts/train/build_tooluse_corpus.py" "scripts/train/serve_eval_shim.py:/workspace/crucix/scripts/train/serve_eval_shim.py" "$DPO_LOCAL:/workspace/datasets/aria_tooluse_dpo_v3.jsonl" "$EVAL_LOCAL:/workspace/datasets/aria_tooluse_eval.jsonl"; do
  src=${item%%:*}; dst=${item#*:}; RSCP "$src" "$dst" || { log "FATAL upload $src"; exit 1; }
done
[ "$RESUME_MODE" = 0 ] || RSCP "$RESUME_REPORT_LOCAL" /workspace/eval/aria_tooluse_dpo_eval.json \
  || { log "FATAL upload resume report"; exit 1; }
mkdir -p "$(dirname "$STATE_FILE")"; { echo "POD_ID=$POD_ID"; echo "HOST=$HOST"; echo "PORT=$PORT"; } > "$STATE_FILE"
if [ "$FRESH_BASE" != 1 ]; then
  TSSH -p "$PORT" root@"$HOST" "POD_ID=$POD_ID RP_KEY='$KEY' DEADLINE=$UPLOAD_DEADLINE GRACE=$GRACE COLLECT_GRACE=$COLLECT_GRACE setsid nohup bash /workspace/pod_selfstop_watch_v04.sh >/workspace/logs/_upload_watch.log 2>&1 </dev/null & echo \$! >/workspace/eval/_watchdog_pid; echo ARMED" | grep -q ARMED || exit 1
  log "uploading recovered SFT adapter with bounded resumable slices"; UPLOAD_OK=0
  for slice in $(seq 1 "$UPLOAD_SLICES"); do
    if TSSH -p "$PORT" root@"$HOST" 'test -f /workspace/aria_tooluse_candidate.tgz' >/dev/null 2>&1; then SFTP_UPLOAD=reput; else SFTP_UPLOAD=put; fi
    log "slice $slice/$UPLOAD_SLICES mode=$SFTP_UPLOAD"
    if printf '%s %s %s\n' "$SFTP_UPLOAD" "$UPLOAD_ADAPTER_LOCAL" /workspace/aria_tooluse_candidate.tgz | timeout "$UPLOAD_SLICE" sftp -b - -i "$KEYF" -o StrictHostKeyChecking=no -o ConnectTimeout=20 -P "$PORT" root@"$HOST" >/dev/null; then UPLOAD_OK=1; break; fi
    STATE=$(pod_state); BYTES=$(TSSH -p "$PORT" root@"$HOST" 'stat -c %s /workspace/aria_tooluse_candidate.tgz 2>/dev/null || echo 0' 2>/dev/null | tr -d '\r[:space:]'); log "slice incomplete bytes=${BYTES:-unknown} state=$STATE"
    [ "$STATE" = RUNNING ] || break
  done
  [ "$UPLOAD_OK" = 1 ] || { log "FATAL bounded adapter upload incomplete"; exit 1; }
  UPLOAD_ADAPTER_SHA256=$(sha256sum "$UPLOAD_ADAPTER_LOCAL" | awk '{print $1}')
  TSSH -p "$PORT" root@"$HOST" "printf '%s  %s\n%s  %s\n%s  %s\n' '$UPLOAD_ADAPTER_SHA256' /workspace/aria_tooluse_candidate.tgz '$DPO_SHA256' /workspace/datasets/aria_tooluse_dpo_v3.jsonl '$EVAL_SHA256' /workspace/datasets/aria_tooluse_eval.jsonl | sha256sum -c - && tar -tzf /workspace/aria_tooluse_candidate.tgz | awk '/\\/adapter_config.json$/ { found=1 } END { exit !found }' && tar -xzf /workspace/aria_tooluse_candidate.tgz -C /workspace/checkpoints" || { log "FATAL remote immutable input validation"; exit 1; }
fi
TSSH -p "$PORT" root@"$HOST" "if [ -s /workspace/eval/_watchdog_pid ]; then kill \$(cat /workspace/eval/_watchdog_pid) 2>/dev/null || true; fi; rm -f /workspace/eval/_cycle_status; POD_ID=$POD_ID RP_KEY='$KEY' DEADLINE=$CYCLE_DEADLINE GRACE=$GRACE COLLECT_GRACE=$COLLECT_GRACE setsid nohup bash /workspace/pod_selfstop_watch_v04.sh >/workspace/logs/_cycle_watch.log 2>&1 </dev/null & echo \$! >/workspace/eval/_watchdog_pid; echo ARMED" | grep -q ARMED || exit 1
POD_ENV="SKIP_TRAIN=$RESUME_MODE FRESH_BASE=$FRESH_BASE EXPECTED_DPO_PAIRS=$EXPECTED_DPO_PAIRS DPO_FILE=/workspace/datasets/aria_tooluse_dpo_v3.jsonl DPO_OUT='$REMOTE_DPO_OUT'"
[ "$FRESH_BASE" = 1 ] || POD_ENV="$POD_ENV SFT_ADAPTER='$REMOTE_SFT_ADAPTER'"
TSSH -p "$PORT" root@"$HOST" "$POD_ENV setsid nohup bash /workspace/pod_tooluse_dpo.sh >/workspace/logs/tooluse_dpo_cycle.log 2>&1 </dev/null & echo STARTED" | grep -q STARTED || exit 1
RSCP_PULL(){ timeout 600 scp -i "$KEYF" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":"$1" "$2" 2>/dev/null; }
log "cycle started"; RC=""
for i in $(seq 1 100); do
  RC=$(TSSH -p "$PORT" root@"$HOST" 'cat /workspace/eval/_cycle_status 2>/dev/null' 2>/dev/null | tr -d '\r[:space:]'); [ -n "$RC" ] && break
  if [ $((i % 5)) -eq 0 ]; then mkdir -p "$(dirname "$OUTPUT_LOCAL")" "$(dirname "$REPORT_LOCAL")"; RSCP_PULL /workspace/eval/aria_tooluse_dpo_adapter.tgz "${OUTPUT_LOCAL}.partial" || true; RSCP_PULL /workspace/eval/aria_tooluse_dpo_eval.json "${REPORT_LOCAL}.partial" || true; fi
  STATE=$(pod_state); [ "$STATE" = NOT_RUNNING ] && break; [ "$STATE" = UNREADABLE ] && log "control plane unreadable"; sleep 90
done
harvest_logs(){ mkdir -p data/eval_reports; RSCP_PULL /workspace/logs/tooluse_dpo_cycle.log data/eval_reports/aria_tooluse_dpo_cycle.log || true; RSCP_PULL /workspace/logs/tooluse_dpo_train.log data/eval_reports/aria_tooluse_dpo_train.log || true; RSCP_PULL /workspace/logs/tooluse_dpo_eval.log data/eval_reports/aria_tooluse_dpo_eval.log || true; }
[ "$RC" = 0 ] || { harvest_logs; log "FATAL cycle rc=${RC:-missing}; diagnostics harvested"; exit 1; }
mkdir -p "$(dirname "$OUTPUT_LOCAL")" "$(dirname "$REPORT_LOCAL")"
RSCP_PULL /workspace/eval/aria_tooluse_dpo_adapter.tgz "$OUTPUT_LOCAL" || exit 1; RSCP_PULL /workspace/eval/aria_tooluse_dpo_eval.json "$REPORT_LOCAL" || exit 1
tar -tzf "$OUTPUT_LOCAL" | awk '/\/adapter_config.json$/ { found=1 } END { exit !found }' || exit 1
"$PYBIN" - "$REPORT_LOCAL" <<'PY' || exit 1
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8")); n=168
assert d.get("complete") is True and d.get("total")==n and len(d.get("rows") or [])==n
print("verified complete held-out DPO report: n=168")
PY
harvest_logs; log "DONE adapter=$OUTPUT_LOCAL report=$REPORT_LOCAL"
