# ARIA-LLM fine-tune — RunPod operator runbook

**R-F819** (2026-05-22) pivots the GPU training path from Fly to RunPod
after Fly announced GPU deprecation (Aug 1) at
https://fly.io/docs/gpus/python-gpu-example/.

The Fly artifacts (`Dockerfile.trainer`, `fly.trainer.toml`, the
`aria-trainer` Fly app, the trainer image in registry) are kept as
a hedge in case Fly reverses; until then, RunPod is the path.

## What's reusable from the Fly work

100% of the actual training code:

- `scripts/train/prepare_sft.py` — SFT dataset assembler (R-F91)
- `scripts/train/sft_train.py` — LoRA SFT trainer (R-F92)
- `scripts/train/prepare_dpo.py` — DPO preference-pair extractor
- `scripts/train/dpo_train.py` — DPO trainer
- `scripts/train/eval_aria_llm.py` — eval harness
- `scripts/train/adapt_chat_audit.py` — chat_audit → harvest adapter (R-F812)
- `scripts/train/runpod_train_pipeline.sh` — orchestration (R-F819)

The Fly-only artifacts (Dockerfile.trainer, fly.trainer.toml) stay in
the repo as deprecated reference — see banner in those files.

## Architecture choice

- **Brain / chat / coding / harvesting stay on Fly aria-intel** (CPU,
  cheap, already configured).
- **GPU workloads (fine-tune + optional serving) go on RunPod.**
- Cross-pod data transfer: SFTP/wget the 1.8MB corpus tarball.

This is the right split — moving the brain to RunPod would be a
~10× cost increase for zero capability gain.

## Cost guide for the v0.1 proof-of-life fine-tune

| GPU | RunPod Community | RunPod Secure | Fits Mistral-7B 4-bit + LoRA? | ETA | Best for |
|---|---|---|---|---|---|
| RTX 4090 24GB | ~$0.34-0.50/hr | n/a | ✅ Yes | 3-4h | Cheapest first-pass |
| **A100 40GB SXM** | **~$1.29/hr** | n/a | ✅ Comfortable | 2-3h | **Recommended** (best $/time) |
| A100 80GB PCIe | ~$1.89/hr | $2.49/hr | ✅ Excessive | 2-3h | When v0.1 jumps to Llama-3.3-70B |

**My recommendation**: A100 40GB Community Cloud (~$3-4 total for proof-of-life).

For v0.1 release scaling up to Llama-3.3-70B (5K-10K pairs):
- 1× A100 80GB Secure Cloud (~$2.49/hr × 6-10h = $15-25)

## Step-by-step

### 1. Pre-flight on aria-intel (corpus size check)

From your local machine:

```powershell
flyctl ssh console -a aria-intel -C "bash -c 'wc -l /data/aria_training/*.jsonl | tail -1'"
```

Need ≥1000 pairs for proof-of-life. If lower, let the output harvester
run a few more days (already enabled per R-F794 on 2026-05-22).

### 2. Export the corpus tarball

From your local machine (same approach used during the Fly attempt
— the corpus is on aria-intel's persistent volume):

```powershell
# On your local:
flyctl ssh console -a aria-intel -C "bash -c 'tar -czf /tmp/aria_corpus.tar.gz -C /data aria_training/'"

# Pull base64-encoded over SSH (workaround for sftp-get path-mangling):
flyctl ssh console -a aria-intel -C "base64 -w 0 /tmp/aria_corpus.tar.gz" > corpus.b64

# Strip the SSH header line + decode:
tail -n +2 corpus.b64 | base64 -d > corpus.tar.gz

# Verify (~1.8MB for 989 pairs, will grow as harvest accumulates)
ls -lh corpus.tar.gz
```

Or if you have direct access to aria-intel via SSH key, use scp.

### 3. Provision the RunPod pod

1. Log into RunPod → **Pods** → **Deploy**
2. **GPU**: `A100 40GB SXM` (recommended) or `A100 80GB PCIe`
3. **Region**: closest to UK operator → `EU-RO-1` (Romania)
4. **Pricing tier**: Community Cloud (~$1.29-1.89/hr; may be interrupted)
   - Switch to Secure Cloud if first run gets interrupted
5. **Template**: `RunPod PyTorch 2.4`
6. **Container disk**: 50 GB
7. **Volume**: 50 GB persistent (this becomes `/workspace`)
8. Click **Deploy**, wait ~30-60s for pod boot.

### 4. Upload the corpus into the pod

Open the pod's **Web Terminal** (RunPod console → Connect → Start
Web Terminal).

**Option A — File Manager (easiest for a 1.8MB file):**
- RunPod console → pod → Files → upload `corpus.tar.gz` to `/workspace/`

**Option B — wget from a temporary signed URL** (e.g., a 1-hour
expiring share link):
```bash
cd /workspace
wget -O corpus.tar.gz "<your signed url>"
```

Then in the Web Terminal:
```bash
cd /workspace
mkdir -p data
tar -xzf corpus.tar.gz -C data/
ls data/aria_training/ | wc -l   # should be 36 files
```

### 5. Clone the repo (so the trainer scripts are available)

```bash
cd /workspace
git clone https://github.com/Arkmurus/crucix.git
```

### 6. Run the training pipeline

Single command — installs deps, runs adapt + prep + SFT + DPO + eval:

```bash
bash /workspace/crucix/scripts/train/runpod_train_pipeline.sh
```

Env overrides if you want to change defaults:

```bash
ARIA_BASE_MODEL=mistralai/Mistral-7B-Instruct-v0.3 \
ARIA_TRAIN_EPOCHS=3 \
ARIA_LORA_RANK=32 \
ARIA_TRAIN_BATCH=2 \
ARIA_SFT_MIN_QUALITY=0.80 \
  bash /workspace/crucix/scripts/train/runpod_train_pipeline.sh
```

For v0.1 release (Llama-3.3-70B, needs more RAM):

```bash
ARIA_BASE_MODEL=meta-llama/Llama-3.3-70B-Instruct \
ARIA_TRAIN_EPOCHS=2 \
ARIA_LORA_RANK=64 \
ARIA_TRAIN_BATCH=1 \
  bash /workspace/crucix/scripts/train/runpod_train_pipeline.sh
```

(Llama-3.3 is HF-gated — run `huggingface-cli login` first.)

### 7. ⚠️ STOP THE POD when done

**This is the cost-meter step.** The pipeline prints the reminder at
the end, but RunPod doesn't auto-stop.

RunPod console → your pod → **Stop**.

If the pod stays running idle, you're paying $1.29-1.89/hr for nothing.

### 8. Connect the trained adapter into ARIA's chain (post-training)

After training, the adapter is at:
- `/workspace/checkpoints/aria_llm_v0_1_dpo/` (if DPO ran)
- `/workspace/checkpoints/aria_llm_v0_1_sft/` (if DPO skipped due to <100 pairs)

**Serving options:**

**A — Deploy a RunPod Serverless Endpoint with vLLM** (best for sporadic use):
```bash
# Still in the pod's Web Terminal:
pip install vllm
python -m vllm.entrypoints.openai.api_server \
    --model mistralai/Mistral-7B-Instruct-v0.3 \
    --enable-lora \
    --lora-modules aria_v0_1=/workspace/checkpoints/aria_llm_v0_1_dpo \
    --port 8000 --host 0.0.0.0
```

Then in RunPod console → expose port 8000 publicly → copy the URL.

From your local:
```powershell
flyctl secrets set ARIA_LLM_URL=https://<runpod-pod>.proxy.runpod.net/v1 -a aria-intel
flyctl secrets set ARIA_LLM_MODEL=aria_v0_1 -a aria-intel
flyctl machines restart -a aria-intel
```

The dormant sovereign tier in `aria_service/llm/fallback.py` activates;
fly logs will show:
```
LLM fallback chain active: aria_llm → deepseek → groq
```

**B — Pull the adapter to local + serve elsewhere:**

From your local:
```powershell
# RunPod console → Files → download the checkpoint folder
# OR via rsync if the pod has SSH enabled
```

You can then serve on a smaller GPU you control, or push to HuggingFace
as a private model and serve via HF Inference Endpoints.

## Re-train cycles

The harvester is always-on (R-F794), so the corpus grows continuously.
Re-fire the pipeline whenever you want a fresh adapter:

1. Re-export corpus tarball from aria-intel (step 2)
2. Re-upload to RunPod pod
3. Bump `ARIA_BASE_MODEL` if scaling up (Mistral-7B → Llama-3.3-70B)
4. Run pipeline → new adapter at `aria_llm_v0_2_*`

Cost per re-train: same range ($3-25 depending on base model and
corpus size).

## When NOT to use RunPod

Skip the RunPod path if:
- Corpus < 1000 pairs (pipeline aborts cheaply, but still spin-up cost)
- You only want to TEST the prep + adapter steps (those run on CPU
  locally — just `python adapt_chat_audit.py`)
- Anthropic billing comes back and DeepSeek+Anthropic chain is enough
  for the current ARIA-Coder use case
