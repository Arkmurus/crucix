#!/usr/bin/env bash
# R-F3420 — HARVEST ONLY. Read the handoff, pull whatever exists, stop the pod.
#
# Short and repeatable by design. It does not wait for the cycle: run it, see
# where things stand, run it again later. Nothing here can be killed at the
# forty-minute mark because nothing here takes forty minutes.
#
# DIAGNOSTICS FIRST (R-F3415). Container disk is ephemeral, so the stop destroys
# the only copy of the run log. The log is what a FAILED cycle leaves behind and
# the result files are what a successful one leaves, so the log is pulled first
# and unconditionally.
#
# STOPPING IS THE DEFAULT. A pod left running is the §24 worst outcome, and this
# script exists precisely because one was. `--leave-running` is the explicit
# opt-out for inspecting a cycle still in progress.
set -uo pipefail
REPO="${REPO:-/c/code/crucix}"; cd "$REPO"
API="https://rest.runpod.io/v1"
KEY=$(grep -E '^RUNPOD_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
[ -n "$KEY" ] || { echo "[harvest] FATAL: RUNPOD_API_KEY not in .env"; exit 1; }

STATE_FILE="${STATE_FILE:-data/eval_reports/.tooluse_pod_state}"
KEEP=0
for a in "$@"; do [ "$a" = "--leave-running" ] && KEEP=1; done

log(){ echo "[$(date -u +%H:%M:%S)] [harvest] $*"; }

# POD_ID may come from the environment (if the state file was lost) or the file.
if [ -z "${POD_ID:-}" ]; then
  [ -f "$STATE_FILE" ] || { log "FATAL: no $STATE_FILE and no POD_ID in env"; exit 1; }
  # shellcheck disable=SC1090
  . "$STATE_FILE"
fi
[ -n "${POD_ID:-}" ] || { log "FATAL: POD_ID unknown"; exit 1; }
log "pod $POD_ID  host ${HOST:-?}:${PORT:-?}  launched $(( ($(date -u +%s) - ${LAUNCHED_AT:-0}) / 60 )) min ago"

KEYF="/tmp/rpkey_harvest"; cp ~/.ssh/runpod_aria "$KEYF"; chmod 600 "$KEYF"
SSH="ssh -i $KEYF -o StrictHostKeyChecking=no -o ConnectTimeout=15"
TSSH(){ timeout 60 $SSH "$@"; }
PULL(){ timeout 120 scp -i "$KEYF" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":"$1" "$2" 2>/dev/null; }

stop_pod(){
  if [ "$KEEP" = 1 ]; then
    log "--leave-running: pod $POD_ID NOT stopped (it self-stops at its deadline)"
    return 0
  fi
  log "stopping pod $POD_ID"
  curl -s -X POST "$API/pods/$POD_ID/stop" -H "Authorization: Bearer $KEY" >/dev/null 2>&1
  return 0
}

RC=$(TSSH -p "$PORT" root@"$HOST" 'cat /workspace/eval/_cycle_status 2>/dev/null' 2>/dev/null | tr -d '\r[:space:]')
if [ -z "$RC" ]; then
  log "cycle has NOT finished yet (no sentinel)"
else
  log "cycle finished with rc=$RC"
fi

mkdir -p data/eval_reports
# The log first, always: it is the only artefact a failed cycle produces.
PULL /workspace/logs/tooluse_cycle.log data/eval_reports/aria_tooluse_cycle_run.log || log "(run log not pulled)"
PULL /workspace/logs/shim_base.log     data/eval_reports/aria_tooluse_shim_base.log    || true
PULL /workspace/logs/shim_trained.log  data/eval_reports/aria_tooluse_shim_trained.log || true
PULL /workspace/logs/sft.log           data/eval_reports/aria_tooluse_cycle_sft.log    || true
PULL /workspace/eval/tooluse_cycle_result.json data/eval_reports/aria_tooluse_cycle_result.json || log "(result json not pulled)"
PULL /workspace/eval/tooluse_eval_base.json    data/eval_reports/aria_tooluse_eval_base.json    || log "(base report not pulled)"
PULL /workspace/eval/tooluse_eval_trained.json data/eval_reports/aria_tooluse_eval_trained.json || log "(trained report not pulled)"

if [ -z "$RC" ]; then
  log "--- run log tail (cycle still in progress) ---"
  tail -20 data/eval_reports/aria_tooluse_cycle_run.log 2>/dev/null || log "(no run log)"
  log "--- end ---"
  log "NOT FINISHED — re-run harvest later, or pass --leave-running to keep waiting"
  [ "$KEEP" = 1 ] || log "NOTE: stopping now ENDS the run; pass --leave-running to let it continue"
  stop_pod
  exit 4
fi

log "=== RESULT ==="
if [ -s data/eval_reports/aria_tooluse_cycle_result.json ]; then
  cat data/eval_reports/aria_tooluse_cycle_result.json
else
  log "NO RESULT PULLED — treat this cycle as FAILED, not as a pass."
  log "--- run log tail (rc=$RC) ---"
  tail -40 data/eval_reports/aria_tooluse_cycle_run.log 2>/dev/null || log "(no run log)"
  log "--- end ---"
fi
[ "$RC" = "0" ] || log "NOTE: cycle rc='$RC' — a non-zero rc is NOT a pass."

stop_pod
log "DONE"
