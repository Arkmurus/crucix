"""R-F1949 — push the trained ARIA-LLM LoRA adapter to Hugging Face so the
RunPod serverless vLLM endpoint can load it.

Default: upload the LoRA adapter dir (small, ~100-300MB) to the HF repo — fast,
no GPU. vLLM serves base + this LoRA (the endpoint loads the adapter on the
base). Use --merge ON A GPU POD to instead merge the adapter into the base and
push a single standalone model (simpler endpoint config, but a ~14GB upload +
needs GPU/RAM — don't run --merge locally on CPU).

Usage:
  HF_TOKEN=... python scripts/train/push_adapter_to_hf.py
  HF_TOKEN=... python scripts/train/push_adapter_to_hf.py --repo aria-intel/aria-llm-grounded-v1 --adapter data/training/checkpoints/aria_llm_grounded_dpo_v1
  HF_TOKEN=... python scripts/train/push_adapter_to_hf.py --merge --base unsloth/mistral-7b-instruct-v0.3   # GPU pod only
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="aria-intel/aria-llm-grounded-v1")
    ap.add_argument("--adapter", default="data/training/checkpoints/aria_llm_grounded_dpo_v1")
    ap.add_argument("--base", default="unsloth/mistral-7b-instruct-v0.3")
    ap.add_argument("--merge", action="store_true", help="merge adapter into base + push standalone (GPU pod only)")
    ap.add_argument("--private", action="store_true", default=True)
    a = ap.parse_args()

    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        print("BLOCKED: HF_TOKEN unset.")
        return 2
    adapter = Path(a.adapter) if Path(a.adapter).is_absolute() else (REPO / a.adapter)
    if not adapter.is_dir() or not (adapter / "adapter_config.json").exists():
        print(f"BLOCKED: no LoRA adapter at {adapter} (need adapter_config.json). "
              "Has the persist run finished + scp'd the adapter back?")
        return 2

    from huggingface_hub import HfApi
    api = HfApi(token=token)
    api.create_repo(repo_id=a.repo, private=a.private, exist_ok=True, repo_type="model")

    if not a.merge:
        # Light path: upload the LoRA adapter dir as-is.
        print(f"[push] uploading LoRA adapter {adapter} -> {a.repo} ...")
        api.upload_folder(folder_path=str(adapter), repo_id=a.repo, repo_type="model",
                          commit_message="R-F1949 grounded SFT+DPO LoRA adapter (judge-DD 0.466 > DeepSeek 0.336)")
        print(f"[push] DONE — adapter at https://huggingface.co/{a.repo}")
        print(f"[push] serve as base+LoRA: base={a.base}, lora={a.repo}; "
              f"set ARIA_LLM_MODEL to the served LoRA name.")
        return 0

    # Heavy path: merge into the base + push a standalone model (GPU pod only).
    print(f"[push] MERGE: loading base {a.base} + adapter {adapter} ...")
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel
    base = AutoModelForCausalLM.from_pretrained(a.base, torch_dtype=torch.bfloat16, device_map="auto")
    merged = PeftModel.from_pretrained(base, str(adapter)).merge_and_unload()
    tok = AutoTokenizer.from_pretrained(a.base)
    print(f"[push] pushing MERGED model -> {a.repo} (~14GB) ...")
    merged.push_to_hub(a.repo, private=a.private, token=token)
    tok.push_to_hub(a.repo, private=a.private, token=token)
    print(f"[push] DONE — merged model at https://huggingface.co/{a.repo}; set the endpoint Model to {a.repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
