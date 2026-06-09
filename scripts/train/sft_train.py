"""sft_train — LoRA SFT trainer for ARIA-LLM v0.1 (R-F92, 2026-05-09).

Runs on a rented GPU (RunPod / Lambda Labs A100 80GB recommended).

Dependencies (install on the GPU host):
  pip install transformers trl peft accelerate bitsandbytes datasets

Inputs:
  --base-model    HuggingFace model id (default: meta-llama/Llama-3.3-70B-Instruct)
  --train-file    JSONL dataset from prepare_sft.py
  --output-dir    where to save the LoRA adapter
  --epochs        default 3
  --lora-rank     default 32
  --lora-alpha    default 64
  --lr            default 2e-5
  --batch-size    default 2 (tuned for A100 80GB; bump for H100)

Output: LoRA adapter at <output-dir>/. Merge into base weights via
merge_and_save.py if desired.

Recommended one-shot run for v0.1:
  python sft_train.py \
    --base-model meta-llama/Llama-3.3-70B-Instruct \
    --train-file /workspace/datasets/aria_sft_v1.jsonl \
    --output-dir /workspace/checkpoints/aria_llm_v0.1_sft \
    --epochs 3 \
    --lora-rank 32

Total compute: ~25-40 GPU-hours on A100 80GB at 5,000-10,000 records.
Cost: ~£60-150 at $1.89/hr (RunPod on-demand rate).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("aria.train.sft")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _import_or_die() -> None:
    """Verify training dependencies are installed before doing anything."""
    missing = []
    for pkg in ("torch", "transformers", "trl", "peft", "datasets"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        logger.error(
            "Missing training dependencies: %s. Install via:\n"
            "    pip install transformers trl peft accelerate bitsandbytes datasets",
            ", ".join(missing),
        )
        sys.exit(1)


def _format_chat(record: dict) -> dict:
    """Normalise an SFT record into a chat `messages` column.

    Accepts BOTH shapes the corpus appears in:
      * messages format — {"messages": [{"role":"user",...}, {"role":"assistant",...}]}
        (the distillation corpus, data/training/aria_sft_distill_*.jsonl)
      * legacy input/output — {"input": "...", "output": "..."} (prepare_sft.py)

    R-F1470: the distillation 500-corpus is messages-format. The old code
    indexed record["input"]/["output"] unconditionally, which KeyErrors on a
    messages-format file — and only AFTER the paid base-model load, wasting the
    whole pod cycle. Normalise here so either shape feeds the trainer directly.
    The trainer's tokenizer applies the chat template at training time."""
    msgs = record.get("messages")
    if isinstance(msgs, list) and msgs:
        return {"messages": msgs}
    return {
        "messages": [
            {"role": "user", "content": record["input"]},
            {"role": "assistant", "content": record["output"]},
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="ARIA-LLM SFT trainer")
    ap.add_argument("--base-model", default="meta-llama/Llama-3.3-70B-Instruct")
    ap.add_argument("--train-file", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lora-rank", type=int, default=32)
    ap.add_argument("--lora-alpha", type=int, default=64)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--batch-size", type=int, default=2)
    ap.add_argument("--max-seq-len", type=int, default=4096)
    ap.add_argument("--load-in-4bit", action="store_true",
                    help="QLoRA — only set if running on a single GPU under 80GB")
    args = ap.parse_args()

    _import_or_die()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Imports are deferred to here so the script's --help works without
    # GPU dependencies installed.
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
    from datasets import load_dataset
    from peft import LoraConfig, get_peft_model, TaskType
    from trl import SFTTrainer, SFTConfig

    logger.info("Loading base model %s", args.base_model)
    bnb_config = None
    if args.load_in_4bit:
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
        )

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        quantization_config=bnb_config,
        trust_remote_code=True,
    )
    model.config.use_cache = False
    model.config.pad_token_id = tokenizer.pad_token_id

    # R-F822 (2026-05-23): live training failure on Mistral-7B fine-tune
    # with `gradient_checkpointing=True` AND LoRA:
    #   RuntimeError: element 0 of tensors does not require grad and
    #   does not have a grad_fn
    # Root cause: gradient checkpointing wraps the forward pass in a
    # way that the input embeddings lose their requires_grad flag, so
    # gradients can't flow back through the LoRA adapter. The canonical
    # fix is enable_input_require_grads() on the base model BEFORE
    # wrapping it with peft. (Equivalent to model.embed_tokens
    # registering a forward hook that sets requires_grad on the output.)
    # See: https://huggingface.co/docs/peft/main/en/developer_guides/troubleshooting
    if getattr(model, "enable_input_require_grads", None):
        model.enable_input_require_grads()

    lora = LoraConfig(
        r=args.lora_rank,
        lora_alpha=args.lora_alpha,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type=TaskType.CAUSAL_LM,
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    logger.info("Loading dataset from %s", args.train_file)
    raw_ds = load_dataset(
        "json",
        data_files=str(args.train_file),
        split="train",
    )
    ds = raw_ds.map(_format_chat, remove_columns=raw_ds.column_names)
    logger.info("Dataset size: %d records", len(ds))

    sft_config = SFTConfig(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=4,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=0.05,
        bf16=True,
        logging_steps=10,
        save_steps=200,
        save_total_limit=3,
        max_seq_length=args.max_seq_len,
        gradient_checkpointing=True,
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=ds,
        args=sft_config,
    )
    trainer.train()
    trainer.save_model(str(args.output_dir))
    tokenizer.save_pretrained(str(args.output_dir))
    logger.info("SFT complete. LoRA adapter saved to %s", args.output_dir)


if __name__ == "__main__":
    main()
