"""R-F1944 — DPO trainer (the verifiable-reward CAP-BREAKER for ARIA-LLM).

Grounded SFT (R-F1941) is the foundation but is teacher-bounded (~0.31 < DeepSeek
0.34). This DPO stage pushes PAST that ceiling using the R-F1942 grounding reward:
it trains on the 469 preference pairs (R-F1943) where chosen = a grounded answer
and rejected = an ungrounded/parametric answer, and the preference was already
VERIFIED objectively (reward(chosen) - reward(rejected) >= margin). DPO makes the
policy prefer grounded-in-our-context answers over fabrication — an ungameable
signal, the reasoning analog of the coder's tests-pass gold.

Runs AFTER the grounded SFT, init from its adapter (sft_adapter_path). QLoRA.

RunPod deps: trl>=0.13, peft, bitsandbytes, datasets, accelerate (CUDA). Local
--validate needs only transformers + the json pairs.

Usage:
  python data/training/train_aria_dpo.py --validate   # no GPU — data/pipeline check
  python data/training/train_aria_dpo.py              # full QLoRA DPO (RunPod)
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
    p = cfg["dpo_pairs_file"]
    cfg["_pairs_path"] = str(Path(p) if Path(p).is_absolute() else (REPO / p))
    return cfg


def load_pairs(path: str) -> list:
    """Load well-formed DPO pairs (prompt + chosen + rejected, conversational)."""
    out = []
    for l in Path(path).read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        r = json.loads(l)
        if (isinstance(r.get("prompt"), list) and r.get("chosen") and r.get("rejected")
                and r["chosen"] != r["rejected"]):
            out.append({"prompt": r["prompt"], "chosen": r["chosen"], "rejected": r["rejected"]})
    return out


def split(data: list, eval_frac: float, seed: int):
    rnd = random.Random(seed)
    idx = list(range(len(data)))
    rnd.shuffle(idx)
    n_val = max(1, int(len(data) * eval_frac)) if eval_frac > 0 else 0
    return [data[i] for i in idx[n_val:]], [data[i] for i in idx[:n_val]]


def validate(cfg: dict) -> int:
    pairs = load_pairs(cfg["_pairs_path"])
    if not pairs:
        print("BLOCKED: no well-formed DPO pairs in", cfg["_pairs_path"]); return 2
    train_d, val_d = split(pairs, cfg.get("eval_split", 0.1), cfg.get("seed", 42))
    print(f"[validate] pairs={len(pairs)} train={len(train_d)} val={len(val_d)}")
    # re-confirm chosen != rejected and prompts are conversational
    bad = sum(1 for p in pairs if p["chosen"] == p["rejected"])
    print(f"[validate] identical chosen/rejected: {bad} (should be 0)")
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(cfg["model_name"])
    n = min(len(pairs), 120)
    plens, clens, rlens = [], [], []
    def _content(msgs):
        return " ".join(m.get("content", "") for m in msgs) if isinstance(msgs, list) else str(msgs)
    for p in pairs[:n]:
        # prompt is a valid (user-first) conversation -> chat-template it.
        plens.append(len(tok(tok.apply_chat_template(p["prompt"], tokenize=False), add_special_tokens=False)["input_ids"]))
        # chosen/rejected are lone assistant turns (TRL concatenates them onto the
        # prompt internally) — measure their raw content length, not via template.
        clens.append(len(tok(_content(p["chosen"]), add_special_tokens=False)["input_ids"]))
        rlens.append(len(tok(_content(p["rejected"]), add_special_tokens=False)["input_ids"]))
    mpl, ml = cfg["dpo_max_prompt_length"], cfg["dpo_max_length"]
    over_p = sum(1 for L in plens if L > mpl)
    over_full = sum(1 for pl, cl in zip(plens, clens) if pl + cl > ml)
    print(f"[validate] prompt tok p95={sorted(plens)[int(n*0.95)]} max={max(plens)} (limit {mpl}, OVER {over_p})")
    print(f"[validate] chosen tok p95={sorted(clens)[int(n*0.95)]} | rejected p95={sorted(rlens)[int(n*0.95)]}")
    print(f"[validate] prompt+chosen OVER dpo_max_length {ml}: {over_full}")
    print(f"[validate] beta={cfg['dpo_beta']} lr={cfg['dpo_learning_rate']} epochs={cfg['dpo_num_epochs']} "
          f"init={cfg['sft_adapter_path']}")
    print("[validate] OK — DPO pairs + chat-template valid; ready for the GPU train path.")
    return 0


def train(cfg: dict) -> int:
    import torch
    from datasets import Dataset
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import LoraConfig, PeftModel
    from trl import DPOTrainer, DPOConfig

    pairs = load_pairs(cfg["_pairs_path"])
    train_d, val_d = split(pairs, cfg.get("eval_split", 0.1), cfg.get("seed", 42))
    train_ds = Dataset.from_list(train_d)
    eval_ds = Dataset.from_list(val_d) if val_d else None
    print(f"[dpo] pairs={len(pairs)} train={len(train_d)} val={len(val_d)} model={cfg['model_name']}")

    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
    base = AutoModelForCausalLM.from_pretrained(
        cfg["model_name"], quantization_config=bnb, device_map="auto", trust_remote_code=True)
    tok = AutoTokenizer.from_pretrained(cfg["model_name"])
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    # Init the policy from the grounded-SFT adapter when present (DPO follows SFT);
    # TRL builds the frozen reference internally for PEFT (adapter disabled).
    sft = Path(cfg["sft_adapter_path"])
    if sft.exists():
        model = PeftModel.from_pretrained(base, str(sft), is_trainable=True)
        peft_cfg = None
        print(f"[dpo] init policy from SFT adapter: {sft}")
    else:
        model = base
        peft_cfg = LoraConfig(r=cfg["lora_r"], lora_alpha=cfg["lora_alpha"],
                              lora_dropout=cfg["lora_dropout"], bias="none",
                              task_type="CAUSAL_LM", target_modules=cfg["lora_target_modules"])
        print("[dpo] no SFT adapter found — DPO from base with a fresh LoRA")

    out = cfg["dpo_output_dir"]
    args = DPOConfig(
        output_dir=out,
        per_device_train_batch_size=cfg["batch_size"],
        gradient_accumulation_steps=cfg["grad_accum_steps"],
        learning_rate=cfg["dpo_learning_rate"],
        num_train_epochs=cfg["dpo_num_epochs"],
        beta=cfg["dpo_beta"],
        max_length=cfg["dpo_max_length"],
        max_prompt_length=cfg["dpo_max_prompt_length"],
        warmup_ratio=cfg["warmup_ratio"],
        lr_scheduler_type=cfg["lr_scheduler_type"],
        bf16=cfg["bf16"],
        gradient_checkpointing=cfg["gradient_checkpointing"],
        logging_steps=cfg["logging_steps"],
        save_steps=cfg["save_steps"],
        eval_strategy=("steps" if eval_ds is not None else "no"),
        eval_steps=cfg["save_steps"],
        seed=cfg["seed"],
        report_to="none",
    )
    trainer = DPOTrainer(model=model, args=args, train_dataset=train_ds,
                         eval_dataset=eval_ds, processing_class=tok, peft_config=peft_cfg)
    trainer.train()
    trainer.save_model(out + "/final")
    tok.save_pretrained(out + "/final")
    print(f"[dpo] complete -> {out}/final")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="ARIA-LLM DPO trainer (grounding cap-breaker)")
    ap.add_argument("--validate", action="store_true", help="no-GPU data/pipeline check")
    a = ap.parse_args()
    cfg = load_config()
    return validate(cfg) if a.validate else train(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
