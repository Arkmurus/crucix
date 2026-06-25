"""R-F1941 — robust grounded-SFT trainer for ARIA-LLM (Track B critical path).

Replaces the 74-line distillation toy. Trains the base model on the 664-example
GROUNDED corpus (chat-messages with grounded/grounded_abstain/abstain labels —
it teaches cite-or-abstain, not fabrication) via QLoRA, with:
  - chat-template formatting + COMPLETION-ONLY loss (train on the assistant
    answer, not the long retrieved context — capacity goes to the skill, not
    reconstructing context)
  - train/val split + eval during training
  - checkpointing, cosine LR + warmup, fixed seed, bf16, gradient checkpointing
  - a --validate mode that checks the data pipeline + token lengths WITHOUT a
    GPU (transformers-only) — the §24 pre-flight pipeline check.

This is grounded SFT (the foundation). The cap-breaker beyond the distillation
ceiling (~0.31 < DeepSeek 0.34) is verifiable-reward (DPO/GRPO) on top — the
next step after this SFT lands.

RunPod deps: trl>=0.13, peft, bitsandbytes, datasets, accelerate (CUDA). Local
--validate needs only transformers + the json corpus.

Usage:
  python data/training/train_aria_llm.py --validate   # no GPU — data/pipeline check
  python data/training/train_aria_llm.py              # full QLoRA SFT (RunPod)
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def load_config() -> dict:
    cfg = json.loads((Path(__file__).parent / "training_config.json").read_text(encoding="utf-8"))
    df = cfg["dataset_file"]
    cfg["_dataset_path"] = str(Path(df) if Path(df).is_absolute() else (REPO / df))
    return cfg


def load_corpus(path: str) -> list:
    """Load well-formed chat rows (user+assistant) from the grounded corpus."""
    rows = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    out = []
    for r in rows:
        msgs = r.get("messages")
        if (isinstance(msgs, list) and len(msgs) >= 2
                and any(m.get("role") == "assistant" and m.get("content") for m in msgs)
                and any(m.get("role") == "user" and m.get("content") for m in msgs)):
            out.append(r)
    return out


def split(data: list, eval_frac: float, seed: int):
    rnd = random.Random(seed)
    idx = list(range(len(data)))
    rnd.shuffle(idx)
    n_val = max(1, int(len(data) * eval_frac)) if eval_frac > 0 else 0
    return [data[i] for i in idx[n_val:]], [data[i] for i in idx[:n_val]]


def validate(cfg: dict) -> int:
    """No-GPU pre-flight: load corpus, split, apply the chat template, report
    token-length distribution vs max_seq_length (truncation risk)."""
    data = load_corpus(cfg["_dataset_path"])
    if not data:
        print("BLOCKED: no well-formed chat rows in", cfg["_dataset_path"]); return 2
    train_d, val_d = split(data, cfg.get("eval_split", 0.1), cfg.get("seed", 42))
    print(f"[validate] corpus={len(data)} train={len(train_d)} val={len(val_d)} "
          f"labels={dict(Counter(r.get('label') for r in data))}")
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(cfg["model_name"])
    n = min(len(data), 150)
    lens = []
    for r in data[:n]:
        text = tok.apply_chat_template(r["messages"], tokenize=False)
        lens.append(len(tok(text, add_special_tokens=False)["input_ids"]))
    lens.sort()
    msl = cfg["max_seq_length"]
    over = sum(1 for L in lens if L > msl)
    print(f"[validate] token len (n={n}): p50={int(statistics.median(lens))} "
          f"p95={lens[int(len(lens)*0.95)]} max={max(lens)} | max_seq_length={msl} | "
          f"OVER limit: {over} ({100*over//max(1,n)}%)")
    print("[validate] formatted sample head:\n   " +
          tok.apply_chat_template(data[0]["messages"], tokenize=False)[:300].replace("\n", "\n   "))
    print("[validate] OK — data pipeline + chat-template valid; ready for the GPU train path.")
    return 0


def train(cfg: dict) -> int:
    import torch
    from datasets import Dataset
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import LoraConfig
    from trl import SFTTrainer, SFTConfig

    data = load_corpus(cfg["_dataset_path"])
    train_d, val_d = split(data, cfg.get("eval_split", 0.1), cfg.get("seed", 42))
    train_ds = Dataset.from_list([{"messages": r["messages"]} for r in train_d])
    eval_ds = Dataset.from_list([{"messages": r["messages"]} for r in val_d]) if val_d else None
    print(f"[train] corpus={len(data)} train={len(train_d)} val={len(val_d)} model={cfg['model_name']}")

    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
    model = AutoModelForCausalLM.from_pretrained(
        cfg["model_name"], quantization_config=bnb, device_map="auto", trust_remote_code=True)
    tok = AutoTokenizer.from_pretrained(cfg["model_name"])
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    lora = LoraConfig(r=cfg["lora_r"], lora_alpha=cfg["lora_alpha"], lora_dropout=cfg["lora_dropout"],
                      bias="none", task_type="CAUSAL_LM", target_modules=cfg["lora_target_modules"])
    out = cfg["output_dir"]
    args = SFTConfig(
        output_dir=out,
        per_device_train_batch_size=cfg["batch_size"],
        gradient_accumulation_steps=cfg["grad_accum_steps"],
        learning_rate=cfg["learning_rate"],
        num_train_epochs=cfg["num_epochs"],
        max_seq_length=cfg["max_seq_length"],
        warmup_ratio=cfg["warmup_ratio"],
        lr_scheduler_type=cfg["lr_scheduler_type"],
        weight_decay=cfg["weight_decay"],
        bf16=cfg["bf16"],
        gradient_checkpointing=cfg["gradient_checkpointing"],
        logging_steps=cfg["logging_steps"],
        save_steps=cfg["save_steps"],
        eval_strategy=("steps" if eval_ds is not None else "no"),
        eval_steps=cfg["save_steps"],
        seed=cfg["seed"],
        completion_only_loss=cfg.get("completion_only_loss", True),
        packing=False,
        report_to="none",
    )
    trainer = SFTTrainer(model=model, args=args, train_dataset=train_ds,
                         eval_dataset=eval_ds, peft_config=lora, processing_class=tok)
    trainer.train()
    trainer.save_model(out + "/final")
    tok.save_pretrained(out + "/final")
    print(f"[train] complete -> {out}/final")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="ARIA-LLM grounded-SFT trainer")
    ap.add_argument("--validate", action="store_true", help="no-GPU data/pipeline check")
    a = ap.parse_args()
    cfg = load_config()
    return validate(cfg) if a.validate else train(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
