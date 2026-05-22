# ARIA-LLM fine-tune — Fly.io operator runbook

**R-F809** consolidates the ARIA-LLM v0.1 fine-tune on Fly.io
(`aria-trainer` app), replacing the cross-cloud RunPod path documented
in `memory/runpod_signed_up.md`.

## What this gets you

A LoRA-fine-tuned model that becomes the **primary** LLM in ARIA's
fallback chain (DeepSeek + Groq drop to fallback positions). The
benefit ARIA-Coder cares about: domain-tuned coding on her own
codebase + defence DD corpus, with the constitutional patterns baked
into the adapter weights.

## Cost meter

| Phase | Cost |
|---|---|
| Build image (no GPU) | <$1 |
| Idle (no machine running) | $0 |
| **A100 80GB running** | **~$3.50/hr** |
| Full v0.1 fine-tune (6-10h) | **$21-35** |
| Re-train after a corpus refresh | $21-35 each time |
| Inference serving (24/7 A100 80GB) | ~$2,500/mo |

**The trainer machine should be stopped immediately after training.**
The pipeline script prints the stop command at the end.

## Prerequisite — corpus size

The pipeline aborts cheaply if the harvest corpus has fewer than
**1,000 pairs**. Check current count from your local machine:

```powershell
curl -s "https://aria-intel.fly.dev/api/aria/harvest/stats" `
  -H "Authorization: Bearer $env:ARIA_API_TOKEN" `
  | python -c "import sys,json; d=json.load(sys.stdin); print(d.get('sft_pairs'))"
```

- `<1000 pairs` → fine-tune will not converge. Let the harvester run.
- `1000-5000 pairs` → proof-of-life run (Mistral-7B base recommended).
- `≥5000 pairs` → v0.1 release run (Llama 3.3 70B base if you want
  to pay for the bigger compute).

`ARIA_OUTPUT_HARVEST_ENABLED=1` is set as of 2026-05-22 (R-F794), so
the corpus grows automatically with chat traffic.

## Setup (one-time)

```powershell
# 1. Create the Fly app
flyctl apps create aria-trainer --org personal

# 2. Create the persistent volume (100GB for corpus + checkpoints + HF cache)
flyctl volumes create aria_trainer_data --region lhr --size 100 -a aria-trainer

# 3. Build the image (no GPU spend — Fly's build machine)
flyctl deploy --config fly.trainer.toml --build-only
```

The image is now sitting in `registry.fly.io/aria-trainer:latest`,
ready to be pulled into a GPU machine on demand.

## Upload the corpus from aria-intel → aria-trainer

Fly volumes are app-scoped, so we copy via SFTP through your local
machine. Cheap (CPU only on both sides):

```powershell
# Pull from aria-intel
flyctl ssh sftp shell -a aria-intel
# in the SFTP shell:
get /data/aria_training/training_data.jsonl C:\tmp\td.jsonl
get /data/aria_training/dpo_training_data.jsonl C:\tmp\dpo.jsonl
exit

# Push to aria-trainer (no GPU running yet — uses a cheap probe machine)
flyctl ssh sftp shell -a aria-trainer
# in the SFTP shell:
mkdir /data/aria_training
put C:\tmp\td.jsonl /data/aria_training/training_data.jsonl
put C:\tmp\dpo.jsonl /data/aria_training/dpo_training_data.jsonl
exit
```

## Run the fine-tune

Single command — launches the A100 80GB machine, runs the pipeline,
prints the stop command:

```powershell
flyctl machine run registry.fly.io/aria-trainer:latest `
  -a aria-trainer `
  --vm-gpu-kind a100-80gb `
  --vm-memory 65536 `
  --vm-cpus 8 `
  --mount source=aria_trainer_data,destination=/data `
  --command "bash /workspace/scripts/train/fly_train_pipeline.sh"
```

Optional env overrides (set via `--env` flags):

```
ARIA_BASE_MODEL=mistralai/Mistral-7B-Instruct-v0.3   # default (smaller, ~3h)
ARIA_BASE_MODEL=meta-llama/Llama-3.3-70B-Instruct    # v0.1 (bigger, ~6-10h)
ARIA_TRAIN_EPOCHS=3
ARIA_LORA_RANK=32
ARIA_TRAIN_BATCH=2
ARIA_SFT_MIN_QUALITY=0.80
```

Watch the run live:

```powershell
flyctl logs -a aria-trainer
```

## STOP THE METER when done

The pipeline prints the stop command on success. Run it immediately:

```powershell
flyctl machine list -a aria-trainer    # find the machine id
flyctl machine stop <id> -a aria-trainer
```

If the pipeline crashes, stop the machine manually — Fly does not
auto-stop on script exit by default.

## Connect the trained model into ARIA's chain

After training:

```powershell
# A future serving R-number will spin up an aria-llm Fly app with
# vLLM mounting the adapter from the aria_trainer_data volume.
# Until then, the adapter sits at:
#   /data/checkpoints/aria_llm_v0_1_dpo/   (final DPO adapter)
# or
#   /data/checkpoints/aria_llm_v0_1_sft/   (if DPO was skipped)
```

When the serving app exists, the final wiring is:

```powershell
flyctl secrets set ARIA_LLM_URL=https://aria-llm.fly.dev/v1 -a aria-intel
flyctl secrets set ARIA_LLM_MODEL=aria_v0_1 -a aria-intel
flyctl machines restart -a aria-intel
```

The dormant sovereign tier in `aria_service/llm/fallback.py` auto-
activates. Fly logs on aria-intel will show:

```
LLM fallback chain active: aria_llm → deepseek → groq
```

## Re-train cycles

The harvester is always-on, so the corpus grows continuously. Re-fire
the pipeline whenever you want a fresh adapter — same command, same
cost, no setup repetition. Old checkpoints are kept under
`/data/checkpoints/aria_llm_v0_2_*/` etc.
