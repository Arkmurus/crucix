#!/usr/bin/env bash
# R-F1772 diagnostic — locate the v0.7 adapter on the volume (pod-self-run found
# "no adapter at /workspace/checkpoints/aria_llm_v0_4_sft"). Boots a tiny pod that
# `find`s adapter_config.json + lists /workspace, POSTs the listing to the brain
# (/eval/external-result as label), self-stops. ZERO client SSH.
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
TOK=$(grep -E '^ARIA_INTERNAL_TOKEN=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
BRAIN="https://aria-intel.fly.dev"
VOL=4vdw2zmqov
GPUS='["NVIDIA A40","NVIDIA RTX A4000","NVIDIA RTX A5000","NVIDIA GeForce RTX 3090","NVIDIA GeForce RTX 4090"]'

read -r -d '' POD <<'PODEOF'
set -uo pipefail
selfstop(){ curl -s -X POST "https://rest.runpod.io/v1/pods/${RUNPOD_POD_ID}/stop" -H "Authorization: Bearer ${RUNPOD_API_KEY}" >/dev/null 2>&1 || true; }
trap selfstop EXIT
ADAPTERS=$(find /workspace -maxdepth 4 -name adapter_config.json 2>/dev/null | head -20)
CKPT=$(ls -la /workspace/checkpoints/ 2>/dev/null | head -30)
TOP=$(ls -la /workspace/ 2>/dev/null | head -30)
DS=$(ls -la /workspace/datasets/ 2>/dev/null | head -20)
SUMMARY="ADAPTERS:[$ADAPTERS] | CHECKPOINTS:[$CKPT] | TOP:[$TOP] | DATASETS:[$DS]"
python - "$SUMMARY" <<'PY'
import json,os,sys,urllib.request
payload={"model":"VOLUME_PROBE","accuracy":None,"label":sys.argv[1][:4000],"source":"runpod_selfrun"}
req=urllib.request.Request(os.environ['ARIA_BRAIN_URL'].rstrip('/')+'/api/aria/eval/external-result',
  data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ['ARIA_TOKEN']})
try: print(urllib.request.urlopen(req,timeout=30).read()[:200])
except Exception as e: print('POST failed',e)
PY
PODEOF
B64=$(printf '%s' "$POD" | base64 -w0 2>/dev/null || printf '%s' "$POD" | base64 | tr -d '\n')
mkjson(){ python - "$VOL" "$GPUS" "echo $B64 | base64 -d | bash" "$TOK" "$BRAIN" "$KEY" <<'PY'
import json,sys
vol,gpus,start,tok,brain,rpkey=sys.argv[1:7]
print(json.dumps({"name":"aria-vol-probe","imageName":"runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",
"gpuTypeIds":json.loads(gpus),"gpuCount":1,"cloudType":"SECURE","networkVolumeId":vol,"volumeMountPath":"/workspace",
"containerDiskInGb":40,"dockerStartCmd":["bash","-c",start],
"env":{"ARIA_TOKEN":tok,"ARIA_BRAIN_URL":brain,"RUNPOD_API_KEY":rpkey}}))
PY
}
POD_ID=""
for i in $(seq 1 12); do
  R=$(curl -s -X POST "$API/pods" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d "$(mkjson)")
  POD_ID=$(echo "$R" | python -c "import sys,json
try: print(json.load(sys.stdin).get('id','') or '')
except: print('')" 2>/dev/null)
  [ -n "$POD_ID" ] && { echo "[probe] created $POD_ID"; break; }
  echo "[probe] no capacity, retry ($i/12)"; sleep 60
done
[ -n "$POD_ID" ] || { echo "[probe] gave up"; exit 2; }
trap "curl -s -X POST $API/pods/$POD_ID/stop -H 'Authorization: Bearer $KEY' >/dev/null 2>&1" EXIT
for i in $(seq 1 15); do
  sleep 40
  R=$(curl -s --max-time 15 -H "Authorization: Bearer $TOK" "$BRAIN/api/aria/eval/external-result" 2>/dev/null)
  echo "$R" | grep -q "VOLUME_PROBE" && { echo "[probe] LISTING:"; echo "$R" | python -c "import sys,json;print(json.load(sys.stdin)['result']['label'])" 2>/dev/null; break; }
  echo "[probe] [$i] waiting for listing..."
done
curl -s -X POST "$API/pods/$POD_ID/stop" -H "Authorization: Bearer $KEY" >/dev/null 2>&1
echo "[probe] done, pod $POD_ID stopped"
