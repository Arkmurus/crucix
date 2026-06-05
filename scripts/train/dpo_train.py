"""dpo_train — DPO trainer for ARIA-LLM v0.1 (R-F92, 2026-05-09).

Runs AFTER sft_train.py. Takes the SFT checkpoint + DPO preference
pairs and produces the final ARIA-LLM v0.1 checkpoint.

Dependencies:
  pip install transformers trl peft accelerate bitsandbytes datasets

Inputs:
  --sft-checkpoint  path to SFT LoRA adapter (output of sft_train.py)
  --base-model      HuggingFace model id (must match SFT base)
  --dpo-file        JSONL from prepare_dpo.py
  --output-dir      where to save the DPO adapter
  --epochs          default 1 (DPO doesn't need many)
  --beta            DPO temperature (default 0.1)
  --lr              default 5e-6 (lower than SFT)

Output: LoRA adapter at <output-dir>/.

Recommended for v0.1:
  python dpo_train.py \
    --base-model meta-llama/Llama-3.3-70B-Instruct \
    --sft-checkpoint /workspace/checkpoints/aria_llm_v0.1_sft \
    --dpo-file /workspace/datasets/aria_dpo_v1.jsonl \
    --output-dir /workspace/checkpoints/aria_llm_v0.1_dpo \
    --epochs 1

Compute: ~10-20 GPU-hours on A100 80GB. ~£20-50 at on-demand rate.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logger = logging.getLogger("aria.train.dpo")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _import_or_die() -> None:
    missing = []
    for pkg in ("torch", "transformers", "trl", "peft", "datasets"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        logger.error("Missing training dependencies: %s", ", ".join(missing))
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description="ARIA-LLM DPO trainer")
    ap.add_argument("--base-model", default="meta-llama/Llama-3.3-70B-Instruct")
    ap.add_argument("--sft-checkpoint", type=Path, required=True)
    ap.add_argument("--dpo-file", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--beta", type=float, default=0.1)
    ap.add_argument("--lr", type=float, default=5e-6)
    ap.add_argument("--batch-size", type=int, default=2)
    ap.add_argument("--max-seq-len", type=int, default=4096)
    ap.add_argument("--load-in-4bit", action="store_true")
    args = ap.parse_args()

    _import_or_die()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
    from datasets import load_dataset
    from peft import PeftModel
    from trl import DPOTrainer, DPOConfig

    logger.info("Loading base + SFT adapter")
    bnb_config = None
    if args.load_in_4bit:
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
        )

    tokenizer = AutoTokenizer.from_pretrained(
        str(args.sft_checkpoint), trust_remote_code=True
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    base = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        quantization_config=bnb_config,
        trust_remote_code=True,
    )
    model = PeftModel.from_pretrained(base, str(args.sft_checkpoint), is_trainable=True)
    model.config.use_cache = False
    model.config.pad_token_id = tokenizer.pad_token_id

    logger.info("Loading DPO dataset")
    ds = load_dataset("json", data_files=str(args.dpo_file), split="train")
    logger.info("DPO pairs: %d", len(ds))

    dpo_config = DPOConfig(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=4,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=0.05,
        bf16=True,
        logging_steps=10,
        save_steps=100,
        save_total_limit=3,
        beta=args.beta,
        max_length=args.max_seq_len,
        max_prompt_length=args.max_seq_len // 2,
        gradient_checkpointing=True,
        report_to="none",
    )

    # R-F1345: trl >=0.12 renamed DPOTrainer's `tokenizer` arg to
    # `processing_class`. Use processing_class (works trl 0.12+); the old
    # `tokenizer=` raised "unexpected keyword" and broke the v0.2 run.
    trainer = DPOTrainer(
        model=model,
        args=dpo_config,
        train_dataset=ds,
        processing_class=tokenizer,
    )
    trainer.train()
    trainer.save_model(str(args.output_dir))
    tokenizer.save_pretrained(str(args.output_dir))
    logger.info("DPO complete. ARIA-LLM v0.1 LoRA at %s", args.output_dir)


if __name__ == "__main__":
    main()
