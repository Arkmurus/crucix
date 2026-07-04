# ARIA-LLM v0.1 — activation runbook (R-F837)

> **⚠️ SUPERSEDED BY THE TWO-TRACK DESIGN (R-F2410, 2026-07-04).** The sections
> below describe the original "sovereign primary for ALL turns" plan. That is NOT
> how activation now works. The proven design is **TWO-TRACK**: the sovereign 7B
> serves **grounded synthesis only** (it beats DeepSeek there — 0.82 vs 0.67
> citation precision, half the fabrication, R-F2397 fresh eval), while DeepSeek
> stays primary for coverage/closed-book/general and is the fallback. **Read the
> "R-F2410 TWO-TRACK ACTIVATION" section at the bottom of this file first** — it is
> the current, authoritative runbook. The legacy steps are kept for reference only.

**Status: READY TO ACTIVATE.** RunPod credit available. Follow the steps below.

## When to activate

DO NOT activate until ALL of these are true:

1. **DPO stage complete** — `/workspace/checkpoints/aria_llm_v0_1_dpo/`
   exists with safetensors. SFT-only (the current state) is unvalidated
   and may regress vs DeepSeek.
2. **Eval gate passed** — run `scripts/train/eval_v01.py` against the
   500-Q frozen eval (Phase A gate #6); v0.1 must beat or match DeepSeek
   on accuracy + verbosity.
3. **Phase A gates closed** — per CLAUDE.md §1, ARIA-LLM serving is
   Phase B/4 work. If gates #3/#5/#7 are still open, this requires
   operator override per CLAUDE.md §1: "I understand Phase A gate #X is
   open. Override anyway."
4. **Budget alignment** — A100 80GB on RunPod is ~$1.89/hr on-demand.
   24/7 = ~$1,360/mo. Operator must explicitly approve since CLAUDE.md
   §17 cost cap is $300/mo.

## Where the adapter lives

```
RunPod pod: aria-trainer  (currently STOPPED — pod-id from RunPod dashboard)
Volume:     aria-training (persistent, survives pod stop)
Path:       /workspace/checkpoints/aria_llm_v0_1_sft/
            ├── adapter_config.json
            ├── adapter_model.safetensors  (~335MB)
            └── tokenizer files
Base:       mistralai/Mistral-7B-Instruct-v0.3 (pulled at serve time)
```

## Activation steps

### 1. Resume the RunPod pod

```bash
# From RunPod dashboard or REST API:
curl -X POST https://api.runpod.io/v2/<pod-id>/resume \
  -H "Authorization: Bearer $RUNPOD_API_KEY"

# Or: dashboard → pods → aria-trainer → Start
# Cold-start ~60-90s (volume re-mount + filesystem check)
```

### 2. Start vLLM with the LoRA adapter

SSH into the pod:

```bash
ssh -i ~/.ssh/runpod_aria root@<pod-host> -p <pod-port>

# Once inside:
cd /workspace
pip install -q vllm  # idempotent, fast if cached

# Start vLLM with LoRA — runs on port 8000 inside the pod
nohup python -m vllm.entrypoints.openai.api_server \
  --model mistralai/Mistral-7B-Instruct-v0.3 \
  --enable-lora \
  --lora-modules aria-llm-v0.1=/workspace/checkpoints/aria_llm_v0_1_sft \
  --max-loras 1 \
  --max-lora-rank 16 \
  --gpu-memory-utilization 0.9 \
  --host 0.0.0.0 --port 8000 \
  > /workspace/logs/vllm_serve.log 2>&1 &

# Wait ~30s for model load + LoRA mount
sleep 35
curl -s http://localhost:8000/v1/models | python -m json.tool
# Should list both `mistralai/Mistral-7B-Instruct-v0.3` and `aria-llm-v0.1`
```

### 3. Expose the endpoint

RunPod auto-exposes the pod's port 8000 at:
```
https://<pod-id>-8000.proxy.runpod.net
```

Verify from your laptop:
```powershell
curl https://<pod-id>-8000.proxy.runpod.net/v1/models
```

### 4. Wire into aria-intel

```bash
# Set on aria-intel ONLY (not aria-web/aria-wa)
flyctl secrets set \
  ARIA_LLM_URL="https://<pod-id>-8000.proxy.runpod.net/v1" \
  ARIA_LLM_MODEL="aria-llm-v0.1" \
  ARIA_LLM_KEY="" \
  -a aria-intel
```

(Empty `ARIA_LLM_KEY` is fine — RunPod proxy URL is unique-token-style.
For real auth, set vLLM `--api-key` flag and put that here.)

Fly will redeploy aria-intel. On boot, `aria_service/llm/fallback.py:509`
detects `ARIA_LLM_URL`, builds the provider, and inserts it at PRIMARY
position. The chain becomes:

```
ARIA-LLM (sovereign) → DeepSeek → Groq → (Anthropic when enabled)
```

### 5. Smoke test the live chain

```bash
# Round-trip a chat through aria-intel
curl -X POST https://aria-intel.fly.dev/api/aria/chat \
  -H "Authorization: Bearer $ARIA_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "What is the OFAC SDN list?"}' \
  | python -m json.tool

# Check the provider field — should be "aria_llm"
```

### 6. Watch latency + cost for 1 hour

```bash
# aria-intel logs — look for "ARIA-LLM (R-F93 sovereign) configured"
flyctl logs -a aria-intel | grep -i "aria_llm\|aria-llm"

# Cost: RunPod dashboard burn rate
# Should be ~$0.05/min steady-state for A100 80GB

# Latency: tokens-per-second
curl -s https://aria-intel.fly.dev/api/aria/cost/monthly/status \
  -H "Authorization: Bearer $ARIA_API_TOKEN" | python -m json.tool
```

## Rollback (if anything goes wrong)

Single env var unset reverts to DeepSeek-primary:

```bash
flyctl secrets unset ARIA_LLM_URL -a aria-intel
# aria-intel redeploys → fallback.py:510 sees empty URL → skips
# ARIA-LLM block entirely → DeepSeek resumes primary
```

Stop the pod:
```bash
# RunPod dashboard → Stop (preserves volume, ends compute billing)
```

## What gets monitored after activation

Add these to operator daily check:

- **Output quality**: `/api/aria/cost/monthly/status` shows provider mix.
  ARIA-LLM should dominate. Anomaly = unexpected fallback cascades.
- **Latency**: chat p95 should be <8s for short queries, <30s for DD.
- **RunPod burn**: dashboard → $/hr stays at ~$1.89 ± idle.
- **Phase A gate #3** (0 fly ERRORs): activation must not regress this.

## Files involved

- `aria_service/llm/aria_llm_provider.py` — OpenAI-compat adapter
  (already wired, env-driven)
- `aria_service/llm/fallback.py:504-528` — chain insertion logic
- `aria_service/llm/tier_router.py` — tier routing (auto-picks ARIA-LLM
  when configured)
- `scripts/train/runpod_train_pipeline.sh` — training pipeline (Stage 2c
  produces SFT, Stage 2d runs DPO)
- `scripts/train/dpo_train.py` — DPO trainer (R-F92, complete)
- `scripts/train/eval_aria_llm.py` — eval harness (R-F94, complete):
  runs R-F80 prompt-injection suite + a defence-DD eval-set, emits a
  Phase 3 exit-criteria report (pi_pass ≥0.90, dd_acc ≥0.80, p50 ≤4s)

## Activation prerequisite — eval set provisioning

`eval_aria_llm.py --eval-set` expects a JSONL of
`{"question", "expected_keywords", "topic"}`. The 500-Q frozen eval
(gate #6, closed 2026-05-19) is generated by `eval_runner.py` /
`eval_golden_seed.py` with `DEFAULT_MAX_GOLDEN = 600`. Before
activation, run something like:

```bash
# 1. Build the 500-Q eval JSONL from the golden seed
# (R-F1335: the old `eval_runner --export` command never existed — this is the real one)
python scripts/train/export_eval_500q.py --out /workspace/datasets/aria_eval_500q.jsonl

# 2. Run the eval against the SFT adapter (served via vLLM, step 3 above)
python scripts/train/eval_aria_llm.py \
  --target https://<pod-id>-8000.proxy.runpod.net/v1 \
  --model aria-llm-v0.1 \
  --eval-set /workspace/datasets/aria_eval_500q.jsonl \
  --out /workspace/eval/aria_llm_v0.1_sft_report.json

# 3. Also run against DeepSeek baseline for comparison
python scripts/train/eval_aria_llm.py \
  --target https://api.deepseek.com/v1 \
  --model deepseek-chat \
  --api-key $DEEPSEEK_API_KEY \
  --eval-set /workspace/datasets/aria_eval_500q.jsonl \
  --out /workspace/eval/deepseek_baseline.json
```

Compare both reports. Activate only if v0.1 ≥ baseline on accuracy AND
prompt-injection pass-rate. SFT-only checkpoint may regress
adversarial robustness — DPO (Stage 2d) is what hardens it. Don't
ship v0.1-SFT to production without running this comparison.

---

# R-F2410 — TWO-TRACK ACTIVATION (current, authoritative)

**Status: READY-TO-FLIP, fully reversible, NOT active.** No live `ARIA_LLM_URL`,
no serving pod, no GPU spend until the operator flips one env var. §16 (activation
gated on Phase A close + operator go), §14 (fallback = operational, not degraded).

## Why two-track (the proven result)

Fresh confirmatory serve, 2026-07-04 (500-Q held-out open-book, scored with the
R-F2397-fixed grounding_reward):

| Metric | Sovereign `aria_llm_grounded_dpo_v1` | DeepSeek |
|---|---|---|
| Citation precision | **0.815** | 0.670 |
| Fabricated citations | **87** | 187 |
| Grounding-reward mean | **0.441** | 0.372 |
| DeepSeek-judge DD acc | **0.426** | 0.336 |
| Answer-rate on answerable (coverage) | 81.3% | **92.7%** |

→ Sovereign wins on grounded+cited+honest synthesis; DeepSeek wins on coverage.
So: **sovereign for grounded synthesis, DeepSeek for everything else + fallback.**

## The router (already in code, default-off)

`aria_service/llm/model_router.py` (R-F2410). Wired at the two synthesis call
sites (§13 mirror): `aria_engine.py` `aria_chat` (`complete_synthesis`) and
`_aria_chat_stream_impl` (`stream_synthesis`).

- **`ARIA_LLM_URL` UNSET (today):** pure pass-through — every synthesis call is
  byte-identical to the current DeepSeek-only path. Nothing changes.
- **`ARIA_LLM_URL` SET:** two-track. A turn is "grounded synthesis" when it carries
  tool/RAG evidence (`[TOOL:`, `[ATTACHED DOCUMENT`, `[I have already run`, or a
  retrieved context ≥200 chars). Grounded → sovereign; else → DeepSeek. Sovereign
  error/timeout/cooldown → DeepSeek fallback, reported **operational** (§14).
- fallback.py no longer makes the sovereign the global chain primary by default
  (it stays DeepSeek-primary); the sovereign is reached only via the router.

### Env knobs
| Var | Effect |
|---|---|
| `ARIA_LLM_URL` | **the flip** — base URL of the served sovereign endpoint (e.g. `https://<pod>-8888.proxy.runpod.net/v1`) |
| `ARIA_LLM_MODEL` | served model id (`aria-llm-grounded-dpo-v1`) |
| `ARIA_LLM_KEY` | optional bearer token for the endpoint |
| `ARIA_LLM_SHADOW=1` | SHADOW: generate sovereign alongside, **ship DeepSeek**, log grounded-rate compare |
| `ARIA_LLM_CANARY_PCT=N` | CANARY: route N% (0-100) of grounded turns to sovereign (stable per session) |
| `ARIA_LLM_TIMEOUT` | per-call sovereign budget in s (default 40) before fallback |
| `ARIA_LLM_ROUTER_DISABLED=1` | hard-off: DeepSeek only even if URL set (incident kill-switch) |
| `ARIA_LLM_PRIMARY_ALL=1` | legacy R-F93 escape hatch — sovereign primary for ALL turns (NOT the default) |

## How to serve (persistent endpoint, §24-aligned)

Adapt the eval serve path (`scripts/train/serve_eval_shim.py`, proven in the
R-F2397 confirmatory run) into a persistent endpoint. **Cheap GPU per §24 shadow
phase: A40 / L40S — NOT A100** (7B bf16 needs ~14GB).

```bash
# 1. Create a volume-free pod (A40/L40S) — reuse scripts/train/_create_v04_pod.py
#    (or a persistent-volume pod so the adapter survives a stop).
# 2. scp the adapter + tokenizer to /workspace/adapter:
#      data/training/checkpoints/aria_llm_grounded_dpo_v1/aria_llm_v0_4_dpo/{adapter_config.json,adapter_model.safetensors}
#      + tokenizer.* from the checkpoint root
# 3. Serve (bf16, OpenAI-compatible, port 8888):
BASE_MODEL=unsloth/mistral-7b-instruct-v0.3 ADAPTER=/workspace/adapter \
  MODEL_NAME=aria-llm-grounded-dpo-v1 PORT=8888 \
  python scripts/train/serve_eval_shim.py
# 4. Expose 8888 via the RunPod proxy → that URL (with /v1) is ARIA_LLM_URL.
```

**§24 scheduler:** once serving, register the pod with `runpod_scheduler`
(`ARIA_RUNPOD_POD_ID` + `RUNPOD_API_KEY`) in **window mode** for the shadow phase
(daily 10:00-18:00 Europe/London auto start/stop; DeepSeek serves off-hours per
§14). Pre-shadow the scheduler stays stop-only. The serve GPU should be A40/L40S
class; A100 only on training days.

### Cost line
- A40 ≈ **$0.44/hr**; L40S ≈ $0.79-1.14/hr. Shadow window 8h/day × A40 ≈
  **~$3.5/day (~$25/wk)**. Full 24/7 A40 ≈ ~$10.5/day (~$317/mo) — so run the
  scheduler window, don't serve 24/7. This is separate from the weekly train
  budget (§24). Any 24/7 serving needs explicit operator approval vs the §17 cap.

## Activation flip / rollback
```bash
# ACTIVATE (operator, after Phase A close + go): set on aria-intel
flyctl secrets set ARIA_LLM_URL="https://<pod>-8888.proxy.runpod.net/v1" \
                   ARIA_LLM_MODEL="aria-llm-grounded-dpo-v1" -a aria-intel
# ROLLBACK (instant, safe): unset → byte-identical DeepSeek-only
flyctl secrets unset ARIA_LLM_URL -a aria-intel
# INCIDENT kill-switch (keep URL, force DeepSeek):
flyctl secrets set ARIA_LLM_ROUTER_DISABLED=1 -a aria-intel
```

## SHADOW → CANARY → FULL ramp (SAFE sequence — documented, execute later)

**Stage A — SHADOW** (`ARIA_LLM_URL` set + `ARIA_LLM_SHADOW=1`): sovereign
generates on every live grounded turn *alongside* DeepSeek; the router logs a
grounded-rate comparison to the brain but **ships DeepSeek's answer** — zero user
risk. Run ≥3-5 days.
- **GO criteria → CANARY:** live sovereign grounded-rate ≥ DeepSeek's on the same
  turns (the eval lift reproduces live); sovereign endpoint uptime/health stable;
  p95 sovereign latency ≤ ~1.5× DeepSeek.
- **NO-GO:** grounded-rate not actually higher live, or endpoint flaps → stay
  DeepSeek, investigate (serving config / prompt), do not proceed.

**Stage B — CANARY** (`ARIA_LLM_SHADOW` unset, `ARIA_LLM_CANARY_PCT=10` → 25 → 50):
sovereign SERVES a small % of grounded turns to real users; DeepSeek serves the
rest and all fallbacks. Watch grounded_rate, fallback rate, latency, user signals.
- **GO criteria → raise %/FULL:** fallback rate low (<~5%); grounded_rate on
  served turns beats the DeepSeek control slice; latency acceptable; no honesty
  regressions (abstention/over-claim within eval bounds).
- **NO-GO / ROLLBACK:** fallback rate high, latency bad, or any grounded/honesty
  regression → lower `ARIA_LLM_CANARY_PCT` or unset `ARIA_LLM_URL`.

**Stage C — FULL** (`ARIA_LLM_CANARY_PCT=100`, the default when set): sovereign
serves all grounded synthesis; DeepSeek remains coverage + fallback. Keep watching
grounded_rate + fallback rate; rollback is always `unset ARIA_LLM_URL`.

**Standing invariant at every stage:** closed-book/general/coverage turns and ALL
fallbacks stay on DeepSeek; a sovereign failure is **operational**, never degraded.
