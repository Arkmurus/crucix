#!/usr/bin/env bash
# R-F3815 — bounded v2-to-v3 DPO continuation and held-out promotion evaluation.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd) || exit 1
REPO="${REPO:-$(cd "$SCRIPT_DIR/../.." && pwd)}"; cd "$REPO" || exit 1
API=https://rest.runpod.io/v1
PYBIN="${PYBIN:-.venv/Scripts/python.exe}"
FRESH_BASE="${FRESH_BASE:-0}"
POD_RUNNER="${POD_RUNNER:-scripts/train/pod_tooluse_dpo.sh}"
TRAINING_RECIPE_KIND="${TRAINING_RECIPE_KIND:-tooluse_dpo_continuation}"
SFT_LOCAL="${SFT_LOCAL:-}"
SFT_SHA256="${SFT_SHA256:-}"
INTERMEDIATE_LOCAL="${INTERMEDIATE_LOCAL:-}"
INTERMEDIATE_REMOTE="${INTERMEDIATE_REMOTE:-/workspace/eval/aria_tooluse_mixed_sft.tgz}"
PROBE_LOCAL="${PROBE_LOCAL:-}"
BASELINE_LOCAL="${BASELINE_LOCAL:-}"
PROBE_SHA256="${PROBE_SHA256:-}"
BASELINE_SHA256="${BASELINE_SHA256:-}"
HELDOUT_BASELINE_LOCAL="${HELDOUT_BASELINE_LOCAL:-}"
HELDOUT_BASELINE_SHA256="${HELDOUT_BASELINE_SHA256:-}"
DIAGNOSTICS_LOCAL="${DIAGNOSTICS_LOCAL:-}"
DIAGNOSTICS_REMOTE="${DIAGNOSTICS_REMOTE:-/workspace/eval/aria_tooluse_curve_diagnostics.tgz}"
EXPECTED_DPO_PAIRS="${EXPECTED_DPO_PAIRS:-8}"
PROTECTED_DPO_AXES="${PROTECTED_DPO_AXES:-}"
DPO_BETA="${DPO_BETA:-0.3}"
DPO_LR="${DPO_LR:-2e-6}"
DPO_GRAD_ACCUM="${DPO_GRAD_ACCUM:-1}"
DPO_EXPECTED_UPDATES="${DPO_EXPECTED_UPDATES:-0}"
SFT_LR="${SFT_LR:-1e-5}"
ADAPTER_LOCAL="${ADAPTER_LOCAL:-data/training/checkpoints/aria_tooluse_dpo_v2.tgz}"
RESUME_ADAPTER_LOCAL="${RESUME_ADAPTER_LOCAL:-}"
RESUME_REPORT_LOCAL="${RESUME_REPORT_LOCAL:-}"
DPO_LOCAL="${DPO_LOCAL:-data/training/aria_tooluse_dpo_v3.jsonl}"
EVAL_LOCAL="${EVAL_LOCAL:-data/training/split_v2/eval.jsonl}"
TRAIN_PROOF="${TRAIN_PROOF:-data/training/tooluse_dpo_generation_v3.jsonl}"
GOLDEN="${GOLDEN:-data/eval_frozen/aria_eval_500q.jsonl}"
REPORT_LOCAL="${REPORT_LOCAL:-data/eval_reports/aria_tooluse_dpo_v3_eval.json}"
FAILURE_DIAGNOSTICS_LOCAL="${FAILURE_DIAGNOSTICS_LOCAL:-${REPORT_LOCAL%.json}_failure.txt}"
OUTPUT_LOCAL="${OUTPUT_LOCAL:-data/training/checkpoints/aria_tooluse_dpo_v3.tgz}"
STATE_FILE="${STATE_FILE:-data/eval_reports/.tooluse_dpo_v3_pod_state}"
EXISTING_POD_ID="${EXISTING_POD_ID:-}"
REMOTE_DPO_OUT="${REMOTE_DPO_OUT:-/workspace/checkpoints/aria_tooluse_dpo_v3}"
ADAPTER_SHA256="${ADAPTER_SHA256:-0fd0b88b16a47bc9276bc1dc96b90a488dad810b8bf296a00147b8fe989f1656}"
DPO_SHA256="${DPO_SHA256:-ef87c13d77e241ca295eb540ed64142e5c3669283b4f3913fa36923c05f5f991}"
EVAL_SHA256=d24be361fb30ff0e51272b2a7338be2924b8df5428d55a469f1c907bd28c3b00
# Separate measured envelopes: prior 311 MB upload <=64 min; 168-row eval ~74 min.
UPLOAD_DEADLINE="${UPLOAD_DEADLINE:-5400}"; CYCLE_DEADLINE="${CYCLE_DEADLINE:-7200}"
MIN_CYCLE_DEADLINE="${MIN_CYCLE_DEADLINE:-0}"
UPLOAD_SLICE="${UPLOAD_SLICE:-720}"; UPLOAD_SLICES="${UPLOAD_SLICES:-7}"
GRACE="${GRACE:-900}"; COLLECT_GRACE="${COLLECT_GRACE:-900}"
MAX_CREATE_TRIES="${MAX_CREATE_TRIES:-15}"; CREATE_RETRY_SECS="${CREATE_RETRY_SECS:-90}"
log(){ echo "[$(date -u +%H:%M:%S)] [tooluse-dpo] $*"; }
case "$CYCLE_DEADLINE" in *[!0-9]*|"") log "FATAL cycle deadline contract must use integer seconds"; exit 3;; esac
case "$MIN_CYCLE_DEADLINE" in *[!0-9]*|"") log "FATAL cycle deadline contract must use integer seconds"; exit 3;; esac
case "$EXISTING_POD_ID" in *[!a-z0-9]*) log "FATAL existing pod id is malformed"; exit 3;; esac
[ "$CYCLE_DEADLINE" -ge "$MIN_CYCLE_DEADLINE" ] || {
  log "FATAL cycle deadline ${CYCLE_DEADLINE}s is below required workload envelope ${MIN_CYCLE_DEADLINE}s"
  exit 3
}
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
[ -z "$SFT_LOCAL" ] || REQUIRED_FILES+=("$SFT_LOCAL")
[ -z "$PROBE_LOCAL" ] || REQUIRED_FILES+=("$PROBE_LOCAL")
[ -z "$BASELINE_LOCAL" ] || REQUIRED_FILES+=("$BASELINE_LOCAL")
[ -z "$HELDOUT_BASELINE_LOCAL" ] || REQUIRED_FILES+=("$HELDOUT_BASELINE_LOCAL")
[ "$FRESH_BASE" = 1 ] || REQUIRED_FILES+=("$UPLOAD_ADAPTER_LOCAL")
for f in "${REQUIRED_FILES[@]}"; do [ -s "$f" ] || { log "FATAL missing $f"; exit 1; }; done
[ "$RESUME_MODE" = 0 ] || [ -s "$RESUME_REPORT_LOCAL" ] || { log "FATAL missing resume report"; exit 1; }
if [ -n "$PROTECTED_DPO_AXES" ]; then
  "$PYBIN" - "$DPO_LOCAL" "$EVAL_LOCAL" "$GOLDEN" "$PROBE_LOCAL" "$PROTECTED_DPO_AXES" <<'PY' || exit 3
import sys
from pathlib import Path

from scripts.train.build_mixed_tooluse_cycle import (
    _load_jsonl,
    _subjects,
    validate_protected_axis_evidence,
)

dpo_path, *forbidden_paths, axes_csv = sys.argv[1:]
forbidden = set()
for path in forbidden_paths:
    if path:
        forbidden.update(_subjects(_load_jsonl(Path(path))))
required = frozenset(axis.strip() for axis in axes_csv.split(",") if axis.strip())
counts = validate_protected_axis_evidence(
    _load_jsonl(Path(dpo_path)),
    forbidden_subjects=forbidden,
    required_axes=required,
)
print("verified protected DPO evidence: " + ", ".join(
    f"{axis}={counts[axis]}" for axis in sorted(required)
))
PY
fi
if [ -n "$PROBE_LOCAL" ]; then
  "$PYBIN" - "$DPO_LOCAL" "$PROBE_LOCAL" <<'PY' || exit 3
import json, sys
from scripts.train.build_tooluse_corpus import _norm_subject

def subjects(path):
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    missing = [i for i, row in enumerate(rows, 1) if not str(row.get("subject") or "").strip()]
    if missing:
        raise SystemExit(f"subject contamination gate: {path} missing subject at rows {missing[:5]}")
    return {_norm_subject(str(row["subject"])) for row in rows}

overlap = sorted(subjects(sys.argv[1]) & subjects(sys.argv[2]))
if overlap:
    raise SystemExit(
        f"subject contamination gate: DPO and calibration overlap on {len(overlap)} subject(s): "
        + ", ".join(overlap[:10])
    )
print("verified DPO/calibration subject disjointness")
PY
fi
if [ -n "$HELDOUT_BASELINE_LOCAL" ]; then
  printf '%s  %s\n' "$HELDOUT_BASELINE_SHA256" "$HELDOUT_BASELINE_LOCAL" \
    | sha256sum -c - || { log "FATAL immutable held-out baseline hash mismatch"; exit 3; }
  "$PYBIN" - "$HELDOUT_BASELINE_LOCAL" "$EVAL_LOCAL" <<'PY' || exit 3
import json, sys
from scripts.train.eval_tooluse import report_consistency_error

baseline = json.load(open(sys.argv[1], encoding="utf-8"))
evaluation = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()]
if baseline.get("complete") is not True or baseline.get("total") != len(evaluation):
    raise SystemExit("held-out baseline is incomplete or has the wrong denominator")
if report_consistency_error(baseline):
    raise SystemExit("held-out baseline summary is inconsistent")
measured = [(row.get("label"), row.get("subject")) for row in baseline.get("rows") or []]
expected = [(row.get("label"), row.get("subject")) for row in evaluation]
if measured != expected:
    raise SystemExit("held-out baseline surface does not match evaluation")
print(f"verified parent held-out baseline: {baseline['honest']}/{baseline['total']}")
PY
fi
EXPECTED_SFT_ROWS=0
if [ -n "$SFT_LOCAL" ]; then
  EXPECTED_SFT_ROWS=$("$PYBIN" -c "import sys; print(sum(bool(x.strip()) for x in open(sys.argv[1], encoding='utf-8')))" "$SFT_LOCAL") \
    || { log "FATAL cannot count SFT rows"; exit 1; }
  [ "$EXPECTED_SFT_ROWS" -gt 0 ] 2>/dev/null \
    || { log "FATAL SFT row count is not positive"; exit 1; }
fi
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
PARENT_MODE=accepted_adapter
[ "$FRESH_BASE" != 1 ] || PARENT_MODE=fresh_base
if [ "$TRAINING_RECIPE_KIND" = tooluse_dpo_balanced_diagnostic_continuation ] \
    || [ "$TRAINING_RECIPE_KIND" = tooluse_dpo_protected_frontier_continuation ]; then
  PARENT_MODE=diagnostic_candidate
fi
case "$TRAINING_RECIPE_KIND" in
  tooluse_dpo_continuation|tooluse_dpo_balanced_diagnostic_continuation|tooluse_dpo_balanced_accepted_continuation|tooluse_dpo_boundary_accepted_continuation|tooluse_dpo_length_controlled_accepted_continuation|tooluse_dpo_protected_frontier_continuation)
    RECIPE_JSON=$(printf '{"kind":"%s","runner":"%s","base_model":"mistralai/Mistral-7B-Instruct-v0.3","epochs":1,"beta":%s,"learning_rate":%s,"batch_size":2,"gradient_accumulation_steps":%s,"expected_optimizer_steps":%s,"max_sequence_length":4096,"max_gradient_norm":0.3,"load_in_4bit":true,"parent_mode":"%s"}' "$TRAINING_RECIPE_KIND" "$POD_RUNNER" "$DPO_BETA" "$DPO_LR" "$DPO_GRAD_ACCUM" "$DPO_EXPECTED_UPDATES" "$PARENT_MODE")
    ;;
  tooluse_positive_sft_continuation)
    RECIPE_JSON=$(printf '{"kind":"tooluse_positive_sft_continuation","runner":"%s","base_model":"mistralai/Mistral-7B-Instruct-v0.3","epochs":1,"learning_rate":1e-5,"batch_size":2,"max_sequence_length":4096,"lora_rank":32,"lora_alpha":64,"load_in_4bit":true,"completion_only_loss":true,"parent_mode":"%s"}' "$POD_RUNNER" "$PARENT_MODE")
    ;;
  tooluse_positive_sft_diagnostic_continuation)
    RECIPE_JSON=$(printf '{"kind":"tooluse_positive_sft_diagnostic_continuation","runner":"%s","base_model":"mistralai/Mistral-7B-Instruct-v0.3","epochs":1,"learning_rate":1e-5,"batch_size":2,"max_sequence_length":4096,"lora_rank":32,"lora_alpha":64,"load_in_4bit":true,"completion_only_loss":true,"parent_mode":"diagnostic_candidate"}' "$POD_RUNNER")
    ;;
  tooluse_positive_sft_scaled_diagnostic_continuation)
    RECIPE_JSON=$(printf '{"kind":"tooluse_positive_sft_scaled_diagnostic_continuation","runner":"%s","base_model":"mistralai/Mistral-7B-Instruct-v0.3","epochs":1,"learning_rate":%s,"batch_size":2,"max_sequence_length":4096,"lora_rank":32,"lora_alpha":64,"load_in_4bit":true,"completion_only_loss":true,"parent_mode":"diagnostic_candidate"}' "$POD_RUNNER" "$SFT_LR")
    ;;
  tooluse_positive_sft_scaled_continuation)
    RECIPE_JSON=$(printf '{"kind":"tooluse_positive_sft_scaled_continuation","runner":"%s","base_model":"mistralai/Mistral-7B-Instruct-v0.3","epochs":1,"learning_rate":%s,"batch_size":2,"max_sequence_length":4096,"lora_rank":32,"lora_alpha":64,"load_in_4bit":true,"completion_only_loss":true,"parent_mode":"%s"}' "$POD_RUNNER" "$SFT_LR" "$PARENT_MODE")
    ;;
  tooluse_adapter_evaluation_recovery)
    RECIPE_JSON=$(printf '{"kind":"tooluse_adapter_evaluation_recovery","runner":"%s","base_model":"mistralai/Mistral-7B-Instruct-v0.3","load_in_4bit":true,"calibration_gate":true,"heldout_rows":168,"parent_mode":"%s"}' "$POD_RUNNER" "$PARENT_MODE")
    ;;
  *) log "FATAL unsupported training recipe kind: $TRAINING_RECIPE_KIND"; exit 3;;
esac
# R-F4270 — a cycle claiming parent_mode=accepted_adapter must NAME the adapter it
# continues from, so the gate can check it against the parent of record. Injected
# once here rather than into six printf templates: the adapter is chosen in one
# place ($ADAPTER_SHA256) and this keeps it that way.
if [ "$PARENT_MODE" = accepted_adapter ]; then
  RECIPE_JSON="${RECIPE_JSON%\}},\"parent_adapter_sha256\":\"$ADAPTER_SHA256\"}"
fi
"$PYBIN" -m scripts.train.preflight_training_recipe --recipe-json "$RECIPE_JSON" || exit 3
"$PYBIN" - "$DPO_LOCAL" "$EXPECTED_DPO_PAIRS" <<'PY' || exit 3
import json, sys
r=[json.loads(x) for x in open(sys.argv[1], encoding="utf-8") if x.strip()]
expected=int(sys.argv[2])
assert len(r)==expected, f"expected {expected} pairs, got {len(r)}"
assert all(x.get("chosen") and x.get("rejected") and x["chosen"]!=x["rejected"] for x in r)
print(f"verified {expected} non-degenerate DPO pairs")
PY
# R-F4246 - refuse a curriculum whose label is predictable from response LENGTH.
# R-F4243 found exactly that in the resolution set by hand, AFTER nine
# candidates had been paid for: 30 of 32 labels recoverable from length alone,
# in opposite directions per branch, so DPO could learn verbosity instead of
# the decision. Finding it by hand is not a control; this runs before the spend.
"$PYBIN" -m scripts.train.preflight_preference_confound --dpo-file "$DPO_LOCAL" || exit 3
if [ "$DPO_EXPECTED_UPDATES" -gt 0 ]; then
  "$PYBIN" - "$EXPECTED_DPO_PAIRS" "$DPO_GRAD_ACCUM" "$DPO_EXPECTED_UPDATES" <<'PY' || exit 3
import math, sys
pairs, accumulation, expected = map(int, sys.argv[1:])
micro_batches = math.ceil(pairs / 2)
if micro_batches % accumulation:
    raise SystemExit(
        f"gradient accumulation truncates the epoch: {micro_batches} micro-batches "
        f"is not divisible by {accumulation}"
    )
updates = micro_batches // accumulation
if updates != expected:
    raise SystemExit(f"expected {expected} optimizer updates, recipe produces {updates}")
print(f"verified complete epoch in {updates} optimizer updates")
PY
fi
POD_ID=""; HOST=""; PORT=""; PREARM_PID=""
PREARM_DEADLINE="${PREARM_DEADLINE:-900}"
disarm_prearm_watchdog(){
  [ -n "$PREARM_PID" ] || return 0
  kill "$PREARM_PID" 2>/dev/null || true
  wait "$PREARM_PID" 2>/dev/null || true
  PREARM_PID=""
}
release(){
  disarm_prearm_watchdog
  [ -n "$POD_ID" ] || return 0
  log "stopping pod $POD_ID"
  for attempt in 1 2 3; do
    curl.exe -s -X POST "$API/pods/$POD_ID/stop" -H "Authorization: Bearer $KEY" >/dev/null 2>&1 || true
    if [ "$(pod_state)" = NOT_RUNNING ]; then log "verified pod $POD_ID stopped"; return 0; fi
    log "stop unverified attempt $attempt/3"; sleep 10
  done
  log "FATAL pod $POD_ID stop unverified after 3 attempts"
  return 1
}
trap release EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
wait_for_pod(){
  HOST=""; PORT=""
  for _ in $(seq 1 40); do
    PD=$(curl.exe -s "$API/pods/$POD_ID" -H "Authorization: Bearer $KEY"); ST=$(printf '%s' "$PD" | jget desiredStatus)
    HOST=$(printf '%s' "$PD" | jget publicIp); PORT=$(printf '%s' "$PD" | pmget)
    if [ "$ST" = RUNNING ]; then
      if [ -n "$HOST" ]; then
        if [ -n "$PORT" ]; then return 0; fi
      fi
    fi
    sleep 10
  done
  return 1
}
if [ -n "$EXISTING_POD_ID" ]; then
  POD_ID="$EXISTING_POD_ID"
  log "starting existing pod $POD_ID for resumable recovery"
  curl.exe -fsS -X POST "$API/pods/$POD_ID/start" \
    -H "Authorization: Bearer $KEY" >/dev/null \
    || { log "FATAL existing pod start rejected"; exit 2; }
  wait_for_pod || { release; POD_ID=""; }
else
  for i in $(seq 1 "$MAX_CREATE_TRIES"); do
    CREATE_ERR=$(mktemp)
    POD_ID=$("$PYBIN" scripts/train/_create_v04_pod.py 2>"$CREATE_ERR" | head -1 | tr -d '[:space:]')
    if [ -z "$POD_ID" ]; then
      CREATE_DETAIL=$(tr '\r\n' '  ' <"$CREATE_ERR" | cut -c1-600)
      rm -f "$CREATE_ERR"
      log "create rejected $i/$MAX_CREATE_TRIES: ${CREATE_DETAIL:-no diagnostic}"
      sleep "$CREATE_RETRY_SECS"
      continue
    fi
    rm -f "$CREATE_ERR"
    if wait_for_pod; then break; fi
    release; POD_ID=""; sleep "$CREATE_RETRY_SECS"
  done
fi
[ -n "$POD_ID" ] && [ -n "$HOST" ] && [ -n "$PORT" ] || { log "BLOCKED no GPU capacity"; exit 2; }
mkdir -p "$(dirname "$STATE_FILE")"
{ echo "POD_ID=$POD_ID"; echo "HOST=$HOST"; echo "PORT=$PORT"; } > "$STATE_FILE"
# Independent of SSH and the native OpenSSH process tree: if the driver wedges
# before it can prove the on-pod watchdog alive, the recorded paid pod still stops.
(
  sleep "$PREARM_DEADLINE"
  curl.exe -s -X POST "$API/pods/$POD_ID/stop" -H "Authorization: Bearer $KEY" >/dev/null 2>&1 || true
) &
PREARM_PID=$!
log "host pre-arm watchdog armed deadline=${PREARM_DEADLINE}s"
KEYF=/tmp/rpkey_tooluse_dpo; cp ~/.ssh/runpod_aria "$KEYF"; chmod 600 "$KEYF"
# RunPod reuses public IP:port endpoints across short-lived pods.  Never consult or
# update the operator's persistent known_hosts file for these ephemeral identities:
# OpenSSH otherwise rejects the replacement key even with StrictHostKeyChecking=no.
SSH_HOST_KEYS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
SSH="ssh -i $KEYF $SSH_HOST_KEYS -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=6"
TSSH(){ timeout 75 $SSH "$@"; }
arm_watchdog(){
  local command=$1
  for attempt in 1 2 3; do
    TSSH -p "$PORT" root@"$HOST" "if [ -s /workspace/eval/_watchdog_pid ]; then kill \$(cat /workspace/eval/_watchdog_pid) 2>/dev/null || true; fi; $command" >/dev/null 2>&1 || true
    if TSSH -p "$PORT" root@"$HOST" 'if test -s /workspace/eval/_watchdog_pid; then kill -0 "$(cat /workspace/eval/_watchdog_pid)"; else exit 1; fi' >/dev/null 2>&1; then
      log "watchdog arm verified"; return 0
    fi
    log "watchdog arm unverified attempt $attempt/3"; sleep 5
  done
  log "FATAL watchdog arm not live after 3 attempts"
  return 1
}
ok=0
for _ in $(seq 1 40); do if TSSH -p "$PORT" root@"$HOST" 'echo ok' 2>/dev/null | grep -q ok; then ok=$((ok+1)); else ok=0; fi; [ "$ok" -ge 3 ] && break; sleep 5; done
[ "$ok" -ge 3 ] || { log "FATAL SSH unstable"; exit 1; }
TSSH -p "$PORT" root@"$HOST" 'mkdir -p /workspace/checkpoints /workspace/datasets /workspace/eval /workspace/logs /workspace/crucix/scripts/train' || exit 1
RSCP(){ timeout 180 scp -i "$KEYF" $SSH_HOST_KEYS -o ConnectTimeout=15 -P "$PORT" "$1" root@"$HOST":"$2" 2>/dev/null; }
for item in "$POD_RUNNER:/workspace/pod_tooluse_dpo.sh" "scripts/train/pod_selfstop_watch_v04.sh:/workspace/pod_selfstop_watch_v04.sh" "scripts/train/dpo_train.py:/workspace/crucix/scripts/train/dpo_train.py" "scripts/train/sft_train.py:/workspace/crucix/scripts/train/sft_train.py" "scripts/train/learning_curve_gate.py:/workspace/crucix/scripts/train/learning_curve_gate.py" "scripts/train/eval_tooluse.py:/workspace/crucix/scripts/train/eval_tooluse.py" "scripts/train/build_tooluse_corpus.py:/workspace/crucix/scripts/train/build_tooluse_corpus.py" "scripts/train/serve_eval_shim.py:/workspace/crucix/scripts/train/serve_eval_shim.py" "$DPO_LOCAL:/workspace/datasets/aria_tooluse_dpo_v3.jsonl" "$EVAL_LOCAL:/workspace/datasets/aria_tooluse_eval.jsonl"; do
  src=${item%%:*}; dst=${item#*:}; RSCP "$src" "$dst" || { log "FATAL upload $src"; exit 1; }
done
[ -z "$SFT_LOCAL" ] || RSCP "$SFT_LOCAL" /workspace/datasets/aria_tooluse_retention_sft.jsonl \
  || { log "FATAL upload $SFT_LOCAL"; exit 1; }
[ -z "$SFT_LOCAL" ] || TSSH -p "$PORT" root@"$HOST" \
  "printf '%s  %s\n' '$SFT_SHA256' /workspace/datasets/aria_tooluse_retention_sft.jsonl | sha256sum -c -" \
  || { log "FATAL remote retention SFT hash mismatch"; exit 1; }
[ -z "$PROBE_LOCAL" ] || RSCP "$PROBE_LOCAL" /workspace/datasets/aria_tooluse_curve_probe.jsonl \
  || { log "FATAL upload $PROBE_LOCAL"; exit 1; }
[ -z "$BASELINE_LOCAL" ] || RSCP "$BASELINE_LOCAL" /workspace/eval/aria_tooluse_curve_raw_probe.json \
  || { log "FATAL upload $BASELINE_LOCAL"; exit 1; }
[ -z "$PROBE_LOCAL" ] || TSSH -p "$PORT" root@"$HOST" \
  "printf '%s  %s\n%s  %s\n' '$PROBE_SHA256' /workspace/datasets/aria_tooluse_curve_probe.jsonl '$BASELINE_SHA256' /workspace/eval/aria_tooluse_curve_raw_probe.json | sha256sum -c -" \
  || { log "FATAL remote curve input hash mismatch"; exit 1; }
[ -z "$HELDOUT_BASELINE_LOCAL" ] || RSCP "$HELDOUT_BASELINE_LOCAL" /workspace/eval/aria_tooluse_parent_heldout.json \
  || { log "FATAL upload held-out baseline"; exit 1; }
[ -z "$HELDOUT_BASELINE_LOCAL" ] || TSSH -p "$PORT" root@"$HOST" \
  "printf '%s  %s\n' '$HELDOUT_BASELINE_SHA256' /workspace/eval/aria_tooluse_parent_heldout.json | sha256sum -c -" \
  || { log "FATAL remote held-out baseline hash mismatch"; exit 1; }
[ "$RESUME_MODE" = 0 ] || RSCP "$RESUME_REPORT_LOCAL" /workspace/eval/aria_tooluse_dpo_eval.json \
  || { log "FATAL upload resume report"; exit 1; }
if [ "$FRESH_BASE" != 1 ]; then
  arm_watchdog "POD_ID=$POD_ID RP_KEY='$KEY' DEADLINE=$UPLOAD_DEADLINE GRACE=$GRACE COLLECT_GRACE=$COLLECT_GRACE setsid nohup bash /workspace/pod_selfstop_watch_v04.sh >/workspace/logs/_upload_watch.log 2>&1 </dev/null & echo \$! >/workspace/eval/_watchdog_pid" || exit 1
  disarm_prearm_watchdog
  log "uploading recovered SFT adapter with bounded resumable slices"; UPLOAD_OK=0
  for slice in $(seq 1 "$UPLOAD_SLICES"); do
    if TSSH -p "$PORT" root@"$HOST" 'test -f /workspace/aria_tooluse_candidate.tgz' >/dev/null 2>&1; then SFTP_UPLOAD=reput; else SFTP_UPLOAD=put; fi
    log "slice $slice/$UPLOAD_SLICES mode=$SFTP_UPLOAD"
    if printf '%s %s %s\n' "$SFTP_UPLOAD" "$UPLOAD_ADAPTER_LOCAL" /workspace/aria_tooluse_candidate.tgz | timeout "$UPLOAD_SLICE" sftp -b - -i "$KEYF" $SSH_HOST_KEYS -o ConnectTimeout=20 -P "$PORT" root@"$HOST" >/dev/null; then UPLOAD_OK=1; break; fi
    STATE=$(pod_state); BYTES=$(TSSH -p "$PORT" root@"$HOST" 'stat -c %s /workspace/aria_tooluse_candidate.tgz 2>/dev/null || echo 0' 2>/dev/null | tr -d '\r[:space:]'); log "slice incomplete bytes=${BYTES:-unknown} state=$STATE"
    if [ "$STATE" = UNREADABLE ]; then
      if TSSH -p "$PORT" root@"$HOST" 'echo upload-pod-alive' 2>/dev/null | grep -q upload-pod-alive; then
        log "control plane unreadable; recorded pod SSH liveness verified"
        STATE=RUNNING
      else
        log "control plane unreadable and recorded pod SSH liveness unverified"
      fi
    fi
    [ "$STATE" = RUNNING ] || break
  done
  [ "$UPLOAD_OK" = 1 ] || { log "FATAL bounded adapter upload incomplete"; exit 1; }
  UPLOAD_ADAPTER_SHA256=$(sha256sum "$UPLOAD_ADAPTER_LOCAL" | awk '{print $1}')
  TSSH -p "$PORT" root@"$HOST" "printf '%s  %s\n%s  %s\n%s  %s\n' '$UPLOAD_ADAPTER_SHA256' /workspace/aria_tooluse_candidate.tgz '$DPO_SHA256' /workspace/datasets/aria_tooluse_dpo_v3.jsonl '$EVAL_SHA256' /workspace/datasets/aria_tooluse_eval.jsonl | sha256sum -c - && tar -tzf /workspace/aria_tooluse_candidate.tgz | awk '/\\/adapter_config.json$/ { found=1 } END { exit !found }' && tar -xzf /workspace/aria_tooluse_candidate.tgz -C /workspace/checkpoints" || { log "FATAL remote immutable input validation"; exit 1; }
fi
arm_watchdog "if [ -s /workspace/eval/_watchdog_pid ]; then kill \$(cat /workspace/eval/_watchdog_pid) 2>/dev/null || true; fi; rm -f /workspace/eval/_cycle_status; POD_ID=$POD_ID RP_KEY='$KEY' DEADLINE=$CYCLE_DEADLINE GRACE=$GRACE COLLECT_GRACE=$COLLECT_GRACE setsid nohup bash /workspace/pod_selfstop_watch_v04.sh >/workspace/logs/_cycle_watch.log 2>&1 </dev/null & echo \$! >/workspace/eval/_watchdog_pid" || exit 1
disarm_prearm_watchdog
POD_ENV="SKIP_TRAIN=$RESUME_MODE FRESH_BASE=$FRESH_BASE EXPECTED_SFT_ROWS=$EXPECTED_SFT_ROWS EXPECTED_DPO_PAIRS=$EXPECTED_DPO_PAIRS DPO_BETA=$DPO_BETA DPO_LR=$DPO_LR DPO_GRAD_ACCUM=$DPO_GRAD_ACCUM SFT_LR=$SFT_LR DPO_FILE=/workspace/datasets/aria_tooluse_dpo_v3.jsonl DPO_OUT='$REMOTE_DPO_OUT'"
[ -z "$HELDOUT_BASELINE_LOCAL" ] || POD_ENV="$POD_ENV HELDOUT_BASELINE=/workspace/eval/aria_tooluse_parent_heldout.json"
[ "$FRESH_BASE" = 1 ] || POD_ENV="$POD_ENV SFT_ADAPTER='$REMOTE_SFT_ADAPTER'"
TSSH -p "$PORT" root@"$HOST" "$POD_ENV setsid nohup bash /workspace/pod_tooluse_dpo.sh >/workspace/logs/tooluse_dpo_cycle.log 2>&1 </dev/null & echo STARTED" | grep -q STARTED || exit 1
RSCP_PULL(){ timeout 600 scp -i "$KEYF" $SSH_HOST_KEYS -P "$PORT" root@"$HOST":"$1" "$2" 2>/dev/null; }
persist_adapter(){
  local remote=$1 destination=$2 download="${2}.download" remote_sha local_sha
  remote_sha=$(TSSH -p "$PORT" root@"$HOST" "sha256sum '$remote' 2>/dev/null | cut -d ' ' -f1" \
    2>/dev/null | tr -d '\r[:space:]')
  [ "${#remote_sha}" = 64 ] || return 1
  # Paid adapters can exceed what one unreliable host connection transfers.
  # Keep the partial and resume it on every harvest instead of restarting at 0.
  printf 'reget %s %s\n' "$remote" "$download" \
    | timeout 600 sftp -b - -i "$KEYF" $SSH_HOST_KEYS -o ConnectTimeout=20 \
        -P "$PORT" root@"$HOST" >/dev/null 2>&1 || true
  local_sha=$(sha256sum "$download" 2>/dev/null | awk '{print $1}')
  [ "$local_sha" = "$remote_sha" ] || return 1
  tar -tzf "$download" | awk '/\/adapter_config.json$/ { found=1 } END { exit !found }' || return 1
  mv "$download" "$destination"
}
persist_report(){
  local remote=$1 destination=$2 download="${2}.download"
  RSCP_PULL "$remote" "$download" || return 1
  "$PYBIN" - "$download" <<'PY' || return 1
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8"))
assert isinstance(d.get("rows"), list) and isinstance(d.get("complete"), bool)
PY
  mv "$download" "$destination"
}
persist_intermediate(){
  [ -n "$INTERMEDIATE_LOCAL" ] || return 1
  mkdir -p "$(dirname "$INTERMEDIATE_LOCAL")"
  persist_adapter "$INTERMEDIATE_REMOTE" "$INTERMEDIATE_LOCAL"
}
persist_diagnostics(){
  [ -n "$DIAGNOSTICS_LOCAL" ] || return 0
  mkdir -p "$(dirname "$DIAGNOSTICS_LOCAL")"
  RSCP_PULL "$DIAGNOSTICS_REMOTE" "${DIAGNOSTICS_LOCAL}.download" || return 1
  tar -tzf "${DIAGNOSTICS_LOCAL}.download" >/dev/null || return 1
  mv "${DIAGNOSTICS_LOCAL}.download" "$DIAGNOSTICS_LOCAL"
}
log "cycle started"; RC=""
OBSERVATION_LOCAL="${REPORT_LOCAL}.cycle_observation"
rm -f "$OBSERVATION_LOCAL" "$FAILURE_DIAGNOSTICS_LOCAL"
observe_cycle(){
  TSSH -p "$PORT" root@"$HOST" 'rc=$(cat /workspace/eval/_cycle_status 2>/dev/null) || exit 4
[ -n "$rc" ] || exit 4
printf "%s\n" "$rc"
if [ "$rc" != 0 ]; then
  printf "%s\n" "__ARIA_FAILURE_BUNDLE_BEGIN__"
  {
    printf "%s\n" "=== eval files ==="
    find /workspace/eval -maxdepth 1 -type f -printf "%f %s bytes\n" 2>&1 | sort
    for file in tooluse_dpo_cycle.log tooluse_dpo_train.log tooluse_dpo_shim.log tooluse_dpo_probe.log tooluse_dpo_eval.log _cycle_watch.log _selfstop_v04.log; do
      printf "=== %s ===\n" "$file"
      tail -160 "/workspace/logs/$file" 2>&1
    done
  } | gzip -c | base64 -w0
  printf "\n%s\n" "__ARIA_FAILURE_BUNDLE_END__"
fi'
}
for i in $(seq 1 100); do
  if observe_cycle > "$OBSERVATION_LOCAL" 2>/dev/null; then
    RC=$("$PYBIN" -m scripts.train.capture_tooluse_cycle_status \
      --input "$OBSERVATION_LOCAL" --failure-out "$FAILURE_DIAGNOSTICS_LOCAL" 2>/dev/null) || {
        log "failure sentinel arrived without valid atomic diagnostics"
        RC=""
      }
  fi
  if [ -n "$RC" ]; then break; fi
  if [ $((i % 5)) -eq 0 ]; then
    mkdir -p "$(dirname "$OUTPUT_LOCAL")" "$(dirname "$REPORT_LOCAL")"
    if [ -n "$INTERMEDIATE_LOCAL" ]; then persist_intermediate || true
    else persist_adapter /workspace/eval/aria_tooluse_dpo_adapter.tgz "${OUTPUT_LOCAL}.partial" || true; fi
    persist_report /workspace/eval/aria_tooluse_dpo_eval.json "${REPORT_LOCAL}.partial" || true
  fi
  STATE=$(pod_state); [ "$STATE" = NOT_RUNNING ] && break; [ "$STATE" = UNREADABLE ] && log "control plane unreadable"; sleep 90
done
harvest_logs(){
  mkdir -p data/eval_reports
  local saved=0
  if RSCP_PULL /workspace/logs/tooluse_dpo_cycle.log data/eval_reports/aria_tooluse_dpo_cycle.log; then saved=1; fi
  if RSCP_PULL /workspace/logs/tooluse_dpo_train.log data/eval_reports/aria_tooluse_dpo_train.log; then saved=1; fi
  if RSCP_PULL /workspace/logs/tooluse_dpo_eval.log data/eval_reports/aria_tooluse_dpo_eval.log; then saved=1; fi
  return $((1-saved))
}
if [ "$RC" != 0 ]; then
  INTERMEDIATE_SAVED=0; REPORT_SAVED=0; DIAGNOSTICS_SAVED=0; LOGS_SAVED=0; ATOMIC_DIAG_SAVED=0
  [ ! -s "$FAILURE_DIAGNOSTICS_LOCAL" ] || ATOMIC_DIAG_SAVED=1
  if [ -n "$INTERMEDIATE_LOCAL" ]; then
    if persist_intermediate; then INTERMEDIATE_SAVED=1; fi
  fi
  if persist_report /workspace/eval/aria_tooluse_dpo_eval.json "${REPORT_LOCAL}.failed"; then REPORT_SAVED=1; fi
  if persist_diagnostics; then DIAGNOSTICS_SAVED=1; fi
  if harvest_logs; then LOGS_SAVED=1; fi
  log "FATAL cycle rc=${RC:-missing}; recovered intermediate=$INTERMEDIATE_SAVED report=$REPORT_SAVED diagnostics=$DIAGNOSTICS_SAVED logs=$LOGS_SAVED atomic_diagnostics=$ATOMIC_DIAG_SAVED"
  exit 1
fi
mkdir -p "$(dirname "$OUTPUT_LOCAL")" "$(dirname "$REPORT_LOCAL")"
persist_report /workspace/eval/aria_tooluse_dpo_eval.json "$REPORT_LOCAL" || exit 1
persist_adapter /workspace/eval/aria_tooluse_dpo_adapter.tgz "$OUTPUT_LOCAL" || exit 1
[ -z "$INTERMEDIATE_LOCAL" ] || persist_intermediate || exit 1
persist_diagnostics || exit 1
"$PYBIN" - "$REPORT_LOCAL" <<'PY' || exit 1
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8")); n=168
assert d.get("complete") is True and d.get("total")==n and len(d.get("rows") or [])==n
print("verified complete held-out DPO report: n=168")
PY
harvest_logs; log "DONE adapter=$OUTPUT_LOCAL report=$REPORT_LOCAL"
