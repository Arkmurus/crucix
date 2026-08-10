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

POLICY_ADAPTER = "default"
REFERENCE_ADAPTER = "reference"


def render_dpo_example(example: dict, tokenizer) -> dict:
    """Render one preference row to TRL's stable string schema."""
    prompt = example.get("prompt")
    chosen = example.get("chosen")
    rejected = example.get("rejected")
    if isinstance(prompt, str):
        prompt = [{"role": "user", "content": prompt}]
    elif not isinstance(prompt, list) or not prompt:
        raise ValueError("DPO prompt must be a non-empty string or message list")
    if not all(isinstance(m, dict) and isinstance(m.get("role"), str)
               and isinstance(m.get("content"), str) for m in prompt):
        raise ValueError("DPO prompt contains an invalid message")

    def completion_text(name: str, value) -> str:
        if isinstance(value, str) and value:
            return value
        if (isinstance(value, list) and len(value) == 1
                and value[0].get("role") == "assistant"
                and isinstance(value[0].get("content"), str)
                and value[0]["content"]):
            return value[0]["content"]
        raise ValueError(f"DPO {name} must be a non-empty assistant completion")

    # R-F3768: TRL 0.12.2's conversational preprocessor left tool-trace prompts
    # as lists and its tokenizer then crashed. Render with the same tokenizer
    # path as sft_train._render_text and serve_eval_shim before DPOTrainer.
    rendered_prompt = tokenizer.apply_chat_template(
        prompt, tokenize=False, add_generation_prompt=True,
    )
    if not isinstance(rendered_prompt, str) or not rendered_prompt:
        raise ValueError("DPO prompt rendered empty")
    return {
        "prompt": rendered_prompt,
        "chosen": completion_text("chosen", chosen),
        "rejected": completion_text("rejected", rejected),
    }


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


def load_continuation_adapters(base, checkpoint: Path, peft_model_cls):
    """Load trainable and frozen copies of the parent for a true DPO reference."""
    model = peft_model_cls.from_pretrained(
        base, str(checkpoint), adapter_name=POLICY_ADAPTER, is_trainable=True,
    )
    model.load_adapter(
        str(checkpoint), adapter_name=REFERENCE_ADAPTER, is_trainable=False,
    )
    model.set_adapter(POLICY_ADAPTER)
    _verify_continuation_adapters(model)
    return model


def _verify_continuation_adapters(model) -> None:
    """Fail closed unless policy and reference weights exist with correct mutability."""
    named = list(model.named_parameters())
    policy = [p for name, p in named if f".{POLICY_ADAPTER}." in name]
    reference = [p for name, p in named if f".{REFERENCE_ADAPTER}." in name]
    if not policy or not any(p.requires_grad for p in policy):
        raise RuntimeError("trainable DPO policy adapter is missing")
    if not reference or any(p.requires_grad for p in reference):
        raise RuntimeError("frozen DPO reference adapter is missing or trainable")


def main() -> None:
    ap = argparse.ArgumentParser(description="ARIA-LLM DPO trainer")
    ap.add_argument("--base-model", default="meta-llama/Llama-3.3-70B-Instruct")
    ap.add_argument("--sft-checkpoint", type=Path)
    ap.add_argument("--fresh-lora", action="store_true",
                    help="initialize a new LoRA from the base instead of continuing an adapter")
    ap.add_argument("--dpo-file", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--beta", type=float, default=0.1)
    ap.add_argument("--lr", type=float, default=5e-6)
    ap.add_argument("--batch-size", type=int, default=2)
    ap.add_argument("--gradient-accumulation-steps", type=int, default=4)
    ap.add_argument("--max-seq-len", type=int, default=4096)
    ap.add_argument("--load-in-4bit", action="store_true")
    # R-F1356: tight gradient clipping to tame the DPO grad-norm explosion
    # (raw grad-norm hit ~17k on 4-bit; even clip-to-1.0 left a bad direction →
    # mode collapse). 0.3 + a low lr + bf16 stabilises the update.
    ap.add_argument("--max-grad-norm", type=float, default=0.3)
    args = ap.parse_args()
    if args.fresh_lora == bool(args.sft_checkpoint):
        ap.error("choose exactly one of --fresh-lora or --sft-checkpoint")

    _import_or_die()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
    from datasets import Dataset, load_dataset
    from peft import PeftModel, LoraConfig, TaskType, get_peft_model
    from trl import DPOTrainer, DPOConfig

    tokenizer_source = args.base_model if args.fresh_lora else str(args.sft_checkpoint)
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_source, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    logger.info("Loading DPO dataset")
    raw_ds = load_dataset("json", data_files=str(args.dpo_file), split="train")
    logger.info("DPO pairs: %d", len(raw_ds))

    # R-F1353: FORMAT CONSISTENCY — the v0.2 collapse root cause.
    # SFT (sft_train.py:_format_chat) trained on conversational `messages`, so
    # SFTTrainer applied the Mistral chat template ([INST]…[/INST]); serving
    # (serve_eval_shim.py) also calls apply_chat_template. But the DPO pairs are
    # RAW STRINGS, which TRL's maybe_apply_chat_template leaves UN-templated —
    # so DPO trained the model on a different prompt format than SFT+serving,
    # dragging it off-distribution into mode-collapse. Render every prompt with
    # the SAME tokenizer template before DPOTrainer sees the dataset.
    rendered_rows = [render_dpo_example(example, tokenizer) for example in raw_ds]
    ds = Dataset.from_list(rendered_rows)
    if not len(ds):
        raise ValueError("DPO dataset is empty")
    first = ds[0]
    if not all(isinstance(first.get(name), str) for name in ("prompt", "chosen", "rejected")):
        raise TypeError("DPO rendered columns are not strings")
    tokenizer(first["prompt"], add_special_tokens=False)
    logger.info("Rendered and tokenized %d string-schema prompts before model load", len(ds))

    logger.info("Loading base%s", " + SFT adapter" if args.sft_checkpoint else " + fresh LoRA")
    bnb_config = None
    if args.load_in_4bit:
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
        )
    base = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        quantization_config=bnb_config,
        trust_remote_code=True,
    )
    if args.fresh_lora:
        if getattr(base, "enable_input_require_grads", None):
            base.enable_input_require_grads()
        model = get_peft_model(base, LoraConfig(
            r=32,
            lora_alpha=64,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                            "gate_proj", "up_proj", "down_proj"],
            lora_dropout=0.05,
            bias="none",
            task_type=TaskType.CAUSAL_LM,
        ))
        adapter_names = {}
    else:
        model = load_continuation_adapters(base, args.sft_checkpoint, PeftModel)
        adapter_names = {
            "model_adapter_name": POLICY_ADAPTER,
            "ref_adapter_name": REFERENCE_ADAPTER,
        }
    model.config.use_cache = False
    model.config.pad_token_id = tokenizer.pad_token_id

    dpo_config = DPOConfig(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=0.05,
        max_grad_norm=args.max_grad_norm,  # R-F1356: tame grad-norm explosion
        bf16=True,
        logging_steps=10,
        save_steps=100,
        save_total_limit=3,
        beta=args.beta,
        max_length=args.max_seq_len,
        max_prompt_length=args.max_seq_len // 2,
        gradient_checkpointing=True,
        report_to="none",
        **adapter_names,
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
    model.save_pretrained(
        str(args.output_dir), selected_adapters=[POLICY_ADAPTER],
    )
    tokenizer.save_pretrained(str(args.output_dir))
    logger.info("DPO complete. ARIA-LLM v0.1 LoRA at %s", args.output_dir)


if __name__ == "__main__":
    main()
