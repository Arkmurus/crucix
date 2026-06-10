# ★ LAUNCH READY — the v0.2-vs-DeepSeek baseline (synced: operator + Claude + ARIA)

Single source of truth for the first action of the training cycle. Stay synchronised.

## TRIGGER
Operator says: **"Claude lets launch aria project"** → Claude runs:
```
bash scripts/train/serve_and_eval_v02.sh
```
and monitors it to completion, then reports the baseline numbers + confirms the pod stopped.

## PRE-FLIGHT DONE (2026-06-08, free — no pod spend, no money)
Ran a read-only GET against the live pod and fixed two launch-blockers BEFORE the morning:
- ✅ **RunPod API corrected (R-F1432 `c0ee6e82`)**: was `api.runpod.io/v2` (serverless) → every pod call would 404. Now `rest.runpod.io/v1` + `/start` + top-level response shape (publicIp + portMappings[22] for SSH) — matches the working `runpod_scheduler.py` and verified against the live pod.
- ✅ **Port fix**: pod `aria-llm-serve` (runpod/pytorch) exposes ONLY `8888/http` + `22/tcp` — **port 8000 is NOT exposed**, so the old `-8000` proxy URL never resolved. vLLM now serves on **8888** (Jupyter freed first) + the `-8888` proxy URL.
- ✅ **Runnable on Windows (R-F1431 `1cb72899`)**: venv python (has httpx) for the local eval, creds auto-loaded from `.env`, pod id defaulted, adapter-base auto-verified == Qwen2.5-14B (aborts on mismatch), 500-Q eval-set pre-exported (`data/eval_reports/aria_eval_500q.jsonl`, 500 lines).
- ✅ Verified live: API endpoint, key, pod id, response shape, exposed ports. `bash -n` clean.

## THE ONE LIVE-ONLY UNKNOWN (honest)
The **SSH → launch-vLLM** chain (SSH auth with the `runpod_aria` key, the running-pod IP/port shape, vLLM binding 8888) can only be exercised on the **first live pod resume** (costs money). It is **cost-safe**: the EXIT trap stops the pod on ANY failure, and SSH/port resolution **fails loud with the raw pod JSON** so if anything differs we see the exact shape and fix it in minutes — never a wasted 45-min load. First run = the real test of this leg.

## WHO DOES WHAT
- **Operator**: say the trigger phrase. Nothing to set up. ~$2-4 (standing weekly approval).
- **Claude**: runs it on the trigger, monitors, reports numbers, confirms pod stopped. Fixes any first-run SSH/port shape issue live.
- **ARIA**: not her lane to execute. Lanes: fix `@-` send bug · hold R-F1426 for operator live-test · held-out split + data engine (forward cycle).

## OUTPUT
- `data/eval_reports/aria_llm_v02_eval.json` (v0.2) + `deepseek_baseline_eval.json` (DeepSeek, same eval set).
- First honest number: v0.2 vs DeepSeek on the frozen 500-Q (v0.2 contaminated/inflated since it trained on them; DeepSeek clean; held-out split fixes forward cycles).

## WEEKLY RHYTHM (UK)
Mon data cut · Tue 09:00-15:00 SFT · Wed 09:00-13:00 DPO · Thu 09:00-11:00 eval · Fri EOD promotion + ARIA scoreboard · daily 07:00 eval+digest · pod OFF outside windows.

## 2026-06-08 EVENING — FIRST LAUNCH ATTEMPTED, BLOCKED ON RUNPOD GPU CAPACITY (operator: review + launch tomorrow)
Operator said the trigger. Ran it. Outcome: **the eval chain itself is now proven-good up to the pod-resume; the ONLY blocker is RunPod has no free A100** for the pinned pod `7ei3hldcpz4j2v` — every `POST /pods/{id}/start` returns `{"error":"start pod: There are not enough free GPUs on the host machine to start this pod.","status":500}`. Pod stayed `EXITED`. **Total spend ~$0** (GPU never started; EXIT trap fired every time).

Two real script bugs were caught + fixed on the way (both were silent — pre-flight only ran `bash -n`, which can't see them):
- **R-F1451** — the `.env` cred-load loop died under `set -euo pipefail`: `grep` for `RUNPOD_POD_ID` (absent in `.env` by design; it has a code default) returned exit 1 → command-substitution → whole script aborted SILENTLY (no output, exit 1) before any echo. Fix: `|| true` on the optional-var load. Verified.
- **R-F1452** — the RunPod start error was written to `start.json` but NEVER printed → a capacity 500 looked like 30 silent "Status: EXITED" polls (5 min wasted) then a misleading SSH-resolve abort. Fix: surface the start error + fail-fast in ~2s with the real reason; capacity errors get an explicit "pinned host has no free GPU — retry/recreate/change-tier" hint. (Also fixed: read start.json via `cat | python` stdin, NOT `open('/tmp/...')` — Windows venv python.exe can't open an MSYS /tmp path.) Verified: now aborts in ~2s with the true cause.

**State for tomorrow:** command is good-to-go; both fixes are in the WORKING TREE (scripts/train/serve_and_eval_v02.sh), reserved as R-F1451/R-F1452, NOT yet committed. The launch is gated ONLY on an A100 being available. Re-run the same one command tomorrow morning once capacity is back:
```
bash scripts/train/serve_and_eval_v02.sh
```
If it still says "not enough free GPUs": that host is full — either wait/retry (now $0 + ~2s per attempt) or recreate the pod on a host with availability (adapter is on the network volume, so a new pod attaching it works; update the POD_ID).

Status: **PAUSED — blocked on RunPod A100 capacity; review + relaunch tomorrow AM.** — Claude, 2026-06-08 evening
