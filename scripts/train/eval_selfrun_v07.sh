#!/usr/bin/env bash
# R-F1772 — POD-SELF-RUN v0.7 eval. ZERO client SSH/SCP — works from ANY OS
# (incl. Windows, where RunPod SCP drops). Creates an on-demand pod whose
# dockerStartCmd runs the ENTIRE eval pod-side from the assets ALREADY on the
# network volume (serve_eval_shim, eval_aria_llm, the 500-Q open-book set, the
# v0.7 adapter — all pushed there by the v0.7 cycle), then POSTs the judge-DD
# result to the brain (/api/aria/eval/external-result) and SELF-STOPS. The
# launcher only makes HTTPS API calls (curl) + polls the brain for the result.
# Does NOT retrain. Does NOT touch ARIA_LLM_URL.
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
TOK=$(grep -E '^ARIA_INTERNAL_TOKEN=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
BRAIN="${ARIA_BRAIN_URL:-https://aria-intel.fly.dev}"
VOL=4vdw2zmqov
GPUS='["NVIDIA A40","NVIDIA RTX A4000","NVIDIA RTX A5000","NVIDIA GeForce RTX 3090","NVIDIA GeForce RTX 4090","NVIDIA L40S","NVIDIA L40","NVIDIA RTX A6000","NVIDIA RTX 6000 Ada Generation","NVIDIA A100 80GB PCIe","NVIDIA A100-SXM4-80GB","NVIDIA A100-SXM4-40GB"]'
[ -n "$KEY" ] || { echo "[selfrun] FATAL RUNPOD_API_KEY missing"; exit 1; }
[ -n "$TOK" ] || { echo "[selfrun] FATAL ARIA_INTERNAL_TOKEN missing"; exit 1; }

# ── The POD-SIDE script (runs entirely on the Linux pod; base64'd to avoid any
#    JSON/shell escaping). Self-contained + always self-stops (cost safety). ──
read -r -d '' POD_SCRIPT <<'PODEOF'
set -uo pipefail
mkdir -p /workspace/logs /workspace/eval
exec >/workspace/logs/selfrun.log 2>&1
echo "[selfrun] $(date -u) start pod=${RUNPOD_POD_ID:-?}"
selfstop(){ echo "[selfrun] self-stop pod ${RUNPOD_POD_ID:-?}"; curl -s -X POST "https://rest.runpod.io/v1/pods/${RUNPOD_POD_ID}/stop" -H "Authorization: Bearer ${RUNPOD_API_KEY}" >/dev/null 2>&1 || true; }
trap selfstop EXIT
cd /workspace
pip install -q "transformers==4.46.3" "peft==0.13.2" "accelerate>=0.34" bitsandbytes sentencepiece protobuf fastapi uvicorn httpx 2>&1 | tail -3
export HF_HOME=/workspace/.cache/huggingface
export BASE_MODEL=unsloth/mistral-7b-instruct-v0.3
EVALSET=/workspace/datasets/aria_eval_openbook.jsonl
SCR=/workspace/crucix/scripts/train
GIT_NOTE=""
discover_evalset(){
  [ -s "$EVALSET" ] && return 0
  echo "[selfrun] expected eval set empty — auto-discovering openbook eval jsonl…"
  C=$(find /workspace -maxdepth 5 -iname '*openbook*.jsonl' 2>/dev/null)
  echo "$C" | sed 's/^/[selfrun]   cand /'
  P=$(echo "$C" | grep -iE 'eval.*openbook|openbook.*eval|500.*openbook' | grep -vi train | head -1)
  [ -z "$P" ] && P=$(echo "$C" | grep -vi train | head -1)
  if [ -n "$P" ]; then EVALSET="$P"; echo "[selfrun] using eval set: $EVALSET"; return 0; fi
  # Not on volume — fetch it from the brain (R-F1774), the reliable transport.
  echo "[selfrun] no eval set on volume — fetching from brain /eval/openbook-set"
  mkdir -p /workspace/datasets
  HTTP=$(curl -s -w '%{http_code}' --max-time 60 -H "Authorization: Bearer ${ARIA_TOKEN}" \
    "${ARIA_BRAIN_URL}/api/aria/eval/openbook-set" -o /workspace/datasets/aria_eval_openbook.jsonl 2>/dev/null)
  if [ "$HTTP" = "200" ] && [ -s /workspace/datasets/aria_eval_openbook.jsonl ]; then
    EVALSET=/workspace/datasets/aria_eval_openbook.jsonl
    GIT_NOTE="brain-fetch http=$HTTP $(wc -l < "$EVALSET")L"
    echo "[selfrun] brain-fetched eval set: $EVALSET ($(wc -l < "$EVALSET") lines)"
    return 0
  fi
  GIT_NOTE="brain-fetch http=$HTTP"
  echo "[selfrun] brain fetch failed: $GIT_NOTE"
}
discover_evalset
PORT=8888; NAME=aria-llm-v0.7
post_result(){ # post_result <json>
  python - "$1" <<'PY'
import json,os,sys,urllib.request
payload=json.loads(sys.argv[1])
req=urllib.request.Request(os.environ['ARIA_BRAIN_URL'].rstrip('/')+'/api/aria/eval/external-result',
  data=json.dumps(payload).encode(), headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ['ARIA_TOKEN']})
try: print('[selfrun] POSTed result:', urllib.request.urlopen(req,timeout=30).read()[:200])
except Exception as e: print('[selfrun] POST failed:', e)
PY
}
# AUTO-DISCOVER the v0.4 SFT adapter (the hardcoded path had no adapter_config.json).
# Prefer a path containing v0_4/v04/sft; else the most recently modified adapter dir.
SFT=/workspace/checkpoints/aria_llm_v0_4_sft
if [ ! -f "$SFT/adapter_config.json" ]; then
  echo "[selfrun] expected adapter path empty — auto-discovering on /workspace…"
  CANDS=$(find /workspace -maxdepth 5 -name adapter_config.json 2>/dev/null)
  echo "[selfrun] adapter_config.json found at:"; echo "$CANDS" | sed 's/^/[selfrun]   /'
  PICK=$(echo "$CANDS" | grep -iE 'v0_?4|sft' | head -1)
  [ -z "$PICK" ] && PICK=$(echo "$CANDS" | xargs -r ls -t 2>/dev/null | head -1)
  [ -n "$PICK" ] && SFT=$(dirname "$PICK")
  echo "[selfrun] using adapter dir: $SFT"
fi
if [ ! -f "$SFT/adapter_config.json" ]; then
  LIST=$(find /workspace -maxdepth 5 -name adapter_config.json 2>/dev/null | head -10 | tr '\n' ';')
  post_result "{\"model\":\"aria-llm-v0.7\",\"accuracy\":null,\"label\":\"FAIL: no adapter on volume (found: ${LIST:-none})\",\"source\":\"runpod_selfrun\"}"; exit 1
fi
if [ ! -s "$EVALSET" ]; then
  post_result "{\"model\":\"aria-llm-v0.7\",\"accuracy\":null,\"label\":\"FAIL: no eval set (git: ${GIT_NOTE:-n/a})\",\"source\":\"runpod_selfrun\"}"; exit 1
fi
echo "[selfrun] eval set ready: $EVALSET ($(wc -l < "$EVALSET") lines)"
# serve the EXISTING adapter (no train)
ADAPTER=$SFT MODEL_NAME=$NAME PORT=$PORT BASE_MODEL=$BASE_MODEL HF_HOME=$HF_HOME setsid nohup python "$SCR/serve_eval_shim.py" >/workspace/logs/shim.log 2>&1 &
served=0; for i in $(seq 1 90); do curl -s --max-time 5 "localhost:$PORT/v1/models" | grep -q "$NAME" && { served=1; echo "[selfrun] serving (try $i)"; break; }; sleep 10; done
if [ "$served" != 1 ]; then tail -30 /workspace/logs/shim.log; post_result '{"model":"aria-llm-v0.7","accuracy":null,"label":"FAIL: shim did not serve","source":"runpod_selfrun"}'; exit 1; fi
cd /workspace/crucix
DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY" python "$SCR/eval_aria_llm.py" --target "http://localhost:$PORT/v1" --model "$NAME" --eval-set "$EVALSET" --out /workspace/eval/v07_rerun.json 2>&1 | tail -25
python - <<'PY'
import json,os,urllib.request
try:
    d=json.load(open('/workspace/eval/v07_rerun.json')); dd=d.get('defence_dd') or {}; pi=d.get('prompt_injection') or {}
    payload={"model":"aria-llm-v0.7","accuracy":dd.get('accuracy'),"leak_rate":pi.get('leak_rate'),"n":dd.get('total'),
             "label":"v0.7 rerun open-book (pod-selfrun)","source":"runpod_selfrun",
             "report":{"defence_dd_accuracy":dd.get('accuracy'),"defence_dd_total":dd.get('total'),"leak_rate":pi.get('leak_rate')}}
except Exception as e:
    payload={"model":"aria-llm-v0.7","accuracy":None,"label":"FAIL eval parse: %s"%e,"source":"runpod_selfrun"}
req=urllib.request.Request(os.environ['ARIA_BRAIN_URL'].rstrip('/')+'/api/aria/eval/external-result',
  data=json.dumps(payload).encode(), headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ['ARIA_TOKEN']})
try: print('[selfrun] POSTed result:', urllib.request.urlopen(req,timeout=30).read()[:200])
except Exception as e: print('[selfrun] POST failed:', e)
PY
echo "[selfrun] done"
PODEOF

B64=$(printf '%s' "$POD_SCRIPT" | base64 -w0 2>/dev/null || printf '%s' "$POD_SCRIPT" | base64 | tr -d '\n')
START="echo $B64 | base64 -d | bash"

mkjson(){ python - "$VOL" "$GPUS" "$START" "$DSK" "$TOK" "$BRAIN" "$KEY" <<'PY'
import json,sys
vol,gpus,start,dsk,tok,brain,rpkey=sys.argv[1:8]
print(json.dumps({
  "name":"aria-v07-selfrun","imageName":"runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",
  "gpuTypeIds":json.loads(gpus),"gpuCount":1,"cloudType":"SECURE",
  "networkVolumeId":vol,"volumeMountPath":"/workspace","containerDiskInGb":80,
  "dockerStartCmd":["bash","-c",start],
  "env":{"DEEPSEEK_API_KEY":dsk,"ARIA_TOKEN":tok,"ARIA_BRAIN_URL":brain,"RUNPOD_API_KEY":rpkey},
}))
PY
}

# pod_status <id> → prints "STATUS|uptime" (e.g. RUNNING|42, EXITED|none)
pod_status(){
  curl -s --max-time 20 "$API/pods/$1" -H "Authorization: Bearer $KEY" 2>/dev/null | python -c "import sys,json
try:
  d=json.load(sys.stdin); print('%s|%s'%(d.get('desiredStatus','?'),(d.get('runtime') or {}).get('uptimeInSeconds','none')))
except: print('?|none')" 2>/dev/null
}
terminate(){ curl -s -X POST "$API/pods/$1/stop" -H "Authorization: Bearer $KEY" >/dev/null 2>&1; curl -s -X DELETE "$API/pods/$1" -H "Authorization: Bearer $KEY" >/dev/null 2>&1; }

# Create a pod AND verify it actually starts. RunPod SECURE intermittently returns a
# pod id that immediately EXITs without ever getting a GPU (uptime None) — those are
# phantoms; terminate + recreate. Only proceed once a pod is genuinely RUNNING.
POD_ID=""
for attempt in $(seq 1 240); do
  R=$(curl -s -X POST "$API/pods" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d "$(mkjson)")
  PID=$(echo "$R" | python -c "import sys,json
try: print(json.load(sys.stdin).get('id','') or '')
except: print('')" 2>/dev/null)
  if [ -z "$PID" ]; then
    ERR=$(echo "$R" | python -c "import sys,json
try: print((json.load(sys.stdin).get('error','') or str(json.load(sys.stdin)))[:70])
except: print('')" 2>/dev/null)
    echo "[selfrun] no capacity ($ERR) — retry 90s ($attempt/240)"; sleep 90; continue
  fi
  echo "[selfrun] created $PID ($attempt/15) — verifying it actually starts…"
  started=0
  for s in $(seq 1 9); do
    sleep 20
    ST=$(pod_status "$PID"); SS=${ST%%|*}; UP=${ST##*|}
    if [ "$SS" = "RUNNING" ] && [ "$UP" != "none" ] && [ "$UP" != "0" ]; then
      started=1; echo "[selfrun] pod $PID RUNNING (uptime ${UP}s) — it will eval + POST + self-stop"; break
    fi
    if [ "$SS" = "EXITED" ] || [ "$SS" = "TERMINATED" ]; then
      echo "[selfrun] phantom $PID ($SS, no GPU) — terminating + recreating"; terminate "$PID"; break
    fi
  done
  [ "$started" = 1 ] && { POD_ID="$PID"; break; }
  # didn't confirm running (stuck pending or exited) — clean up and recreate
  terminate "$PID"; sleep 10
done
[ -n "$POD_ID" ] || { echo "[selfrun] GAVE UP — could not get a pod to actually RUN (capacity flaky)"; exit 2; }

# Backstop force-stop (in case the pod's self-stop fails) — launcher owns the key.
force_stop(){ curl -s -X POST "$API/pods/$POD_ID/stop" -H "Authorization: Bearer $KEY" >/dev/null 2>&1 || true; }
trap force_stop EXIT

echo "[selfrun] polling brain for the result (cap ~50 min)…"
for i in $(seq 1 50); do
  sleep 60
  RES=$(curl -s --max-time 15 -H "Authorization: Bearer $TOK" "$BRAIN/api/aria/eval/external-result" 2>/dev/null)
  HAS=$(echo "$RES" | python -c "import sys,json
try:
  d=json.load(sys.stdin); r=d.get('result') or {}
  print('YES' if (r.get('source')=='runpod_selfrun' and (r.get('accuracy') is not None or 'FAIL' in (r.get('label') or ''))) else '')
except: print('')" 2>/dev/null)
  [ "$HAS" = "YES" ] && { echo "[selfrun] RESULT RECEIVED:"; echo "$RES" | python -m json.tool 2>/dev/null | head -20; break; }
  echo "[selfrun] [$i/50] result not in yet…"
done
force_stop
echo "[selfrun] DONE — pod $POD_ID stopped."
