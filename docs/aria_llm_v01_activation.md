# ARIA-LLM v0.1 — activation runbook (R-F837)

**Status: NOT ACTIVE.** Adapter trained but unwired. This file is the
flip-the-switch procedure for when activation criteria are met.

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
  produces the SFT adapter; 2d would produce DPO)
- `scripts/train/eval_v01.py` — **DOES NOT EXIST YET** (R-F838 candidate)

## Next R-numbers blocking activation

- **R-F838** (proposed) — `eval_v01.py`: run 500-Q frozen eval against
  v0.1 SFT, compare vs DeepSeek baseline, emit pass/fail. Required
  before activation per gate #6.
- **R-F839** (proposed) — DPO stage script: full Stage 2d of the
  pipeline. Currently stub'd.
