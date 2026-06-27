#!/usr/bin/env python
"""R-F2010 — GRPO RLVR trainer: optimise ARIA-LLM DIRECTLY against the objective
grounding reward (aria_service/intel/grounding_reward.py, R-F1942).

Why GRPO and not (more) DPO: the prior grounded-DPO cycle (2026-06-27) trained on
static pairs whose `rejected` was DeepSeek's *parametric* answer — it taught
ABSTENTION (84% abstain) but NEVER contrasted a grounded citation against a
FABRICATED one, so the model still invented [Source:] tags 92% of the time
(objective grounding 0.21, citation precision 0.08, 527 fabricated). GRPO fixes
this at the root: the model generates G completions per prompt ON-POLICY, each is
scored by the verifiable grounding reward (fabricated citation -> ~0), and the
policy is pushed toward its own highest-grounding samples. The reward is the same
ungameable signal the objective eval uses — so we optimise the metric we're judged
on, not a proxy.

Reward contract (trl GRPO): reward_funcs receive (prompts, completions, **columns)
where each dataset column (here `context`) arrives as a list aligned to the
flattened completions. We score grounding_reward.reward(completion_text, context).

Runs on the pod (trl>=0.14 for GRPOTrainer). `--dry-run` validates the dataset +
exercises the reward fn with NO GPU / NO trl import, so the pipeline is pre-flighted
locally before any paid GPU (CLAUDE.md §24).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from aria_service.intel import grounding_reward as gr  # local, no heavy deps


def _completion_text(c) -> str:
    """A GRPO completion is either a raw string or a conversational list
    [{'role':'assistant','content': ...}]. Normalise to the text."""
    if isinstance(c, str):
        return c
    if isinstance(c, list) and c:
        last = c[-1]
        if isinstance(last, dict):
            return last.get("content", "") or ""
    if isinstance(c, dict):
        return c.get("content", "") or ""
    return str(c or "")


def make_reward_fn():
    """Build the GRPO reward function bound to the grounding reward. Returns a
    list[float] in [0,1] — fabricated citations score ~0, grounded/honest-abstain
    score high. Robust to a missing/short context (reward handles empty context)."""
    def grounding_reward_fn(prompts=None, completions=None, **kwargs):
        contexts = kwargs.get("context")
        answerables = kwargs.get("answerable")   # R-F2033 — per-prompt answerability
        comps = completions or []
        if contexts is None:
            # Fall back to the prompt text as context (it carries the [Source:]
            # markers) so the reward can still verify citations.
            contexts = [_prompt_text(p) for p in (prompts or [])]
        out = []
        for i, c in enumerate(comps):
            ctx = contexts[i] if i < len(contexts) else ""
            ans = answerables[i] if (answerables is not None and i < len(answerables)) else None
            out.append(float(gr.reward(_completion_text(c), ctx or "", answerable=ans)))
        return out
    grounding_reward_fn.__name__ = "grounding_reward"
    return grounding_reward_fn


def _prompt_text(p) -> str:
    if isinstance(p, str):
        return p
    if isinstance(p, list) and p:
        return " ".join(m.get("content", "") for m in p if isinstance(m, dict))
    return str(p or "")


def load_dataset(path: Path) -> list[dict]:
    """Load the GRPO prompt dataset. Each line: {"prompt":[{role,content}],
    "context": "<text with [Source:] markers>"}."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            d = json.loads(ln)
            if "prompt" in d and "context" in d:
                rows.append(d)
    return rows


def dry_run(ds_path: Path) -> int:
    """No-GPU pre-flight: validate dataset shape + exercise the ANSWERABLE-AWARE
    reward (R-F2033). On an ANSWERABLE prompt a grounded answer must beat abstaining
    (the fix for GRPO's abstention-gaming); on an UNANSWERABLE prompt abstaining
    must beat answering. Fabrication is always worst. CLAUDE.md §24."""
    rows = load_dataset(ds_path)
    assert rows, f"empty dataset: {ds_path}"
    bad = [i for i, r in enumerate(rows)
           if not isinstance(r.get("prompt"), list) or not r.get("context")
           or "answerable" not in r]
    assert not bad, f"{len(bad)} rows missing prompt(list)/context/answerable (e.g. idx {bad[:3]})"
    n_ans = sum(1 for r in rows if r.get("answerable"))
    print(f"[dry-run] dataset OK: {len(rows)} prompts (answerable={n_ans}, "
          f"unanswerable={len(rows) - n_ans}), all have prompt+context+answerable")

    rf = make_reward_fn()
    ans_row = next((r for r in rows if r.get("answerable")), rows[0])
    unans_row = next((r for r in rows if not r.get("answerable")), None)
    ctx = ans_row["context"]
    real_src = (gr.extract_citations(ctx) or ["web_search:example"])[0]
    grounded = f"Per the evidence, the figure is X [Source: {real_src}]."
    fabricated = "The figure is definitely 12345 [Source: madeup_source_99]."
    abstain = "Based solely on the context, I cannot confirm that."

    # ANSWERABLE prompt: grounded answering must beat abstaining (and fabrication).
    r_g, r_f, r_a = rf(completions=[grounded, fabricated, abstain],
                       context=[ctx, ctx, ctx], answerable=[True, True, True])
    print(f"[dry-run] ANSWERABLE: grounded={r_g:.3f}  abstain={r_a:.3f}  fabricated={r_f:.3f}")
    assert r_g > r_a, f"on answerable, grounded({r_g}) must beat abstain({r_a}) — the R-F2033 fix"
    assert r_g > r_f and r_a >= r_f, "fabrication must be worst"

    if unans_row is not None:
        uctx = unans_row["context"]
        usrc = (gr.extract_citations(uctx) or ["web_search:example"])[0]
        ugrounded = f"The figure is X [Source: {usrc}]."
        r_ua, r_ug = rf(completions=[abstain, ugrounded],
                        context=[uctx, uctx], answerable=[False, False])
        print(f"[dry-run] UNANSWERABLE: abstain={r_ua:.3f}  answered={r_ug:.3f}")
        assert r_ua > r_ug, f"on unanswerable, abstain({r_ua}) must beat answering({r_ug})"

    print("[dry-run] PASS — answerable-aware reward ranks grounded-answer > abstain on "
          "answerable Qs and abstain > answer on unanswerable Qs. Abstention-gaming closed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="ARIA-LLM GRPO RLVR trainer (grounding reward)")
    ap.add_argument("--base-model", default="unsloth/mistral-7b-instruct-v0.3")
    ap.add_argument("--sft-checkpoint", type=Path, default=None,
                    help="optional SFT/DPO adapter to start GRPO from (PeftModel init)")
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, default=Path("/workspace/checkpoints/aria_llm_grpo_v1"))
    ap.add_argument("--num-generations", type=int, default=6)
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--lr", type=float, default=1e-6)
    ap.add_argument("--beta", type=float, default=0.04, help="KL coeff to the ref policy")
    ap.add_argument("--batch-size", type=int, default=6, help="prompts/device; must be divisible by num-generations effective grouping")
    ap.add_argument("--max-prompt-len", type=int, default=3072)
    ap.add_argument("--max-completion-len", type=int, default=640)
    ap.add_argument("--load-in-4bit", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="validate dataset+reward, NO GPU/trl")
    args = ap.parse_args()

    if args.dry_run:
        return dry_run(args.dataset)

    # ---- real training (pod, trl>=0.14) ----
    import torch
    from datasets import Dataset
    from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
    from peft import LoraConfig, PeftModel
    from trl import GRPOTrainer, GRPOConfig

    rows = load_dataset(args.dataset)
    print(f"[grpo] {len(rows)} prompts from {args.dataset}")
    ds = Dataset.from_list([
        {"prompt": r["prompt"], "context": r["context"],
         "answerable": bool(r.get("answerable", False))}   # R-F2033
        for r in rows
    ])

    quant = None
    if args.load_in_4bit:
        quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                   bnb_4bit_compute_dtype=torch.bfloat16,
                                   bnb_4bit_use_double_quant=True)
    tok = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=False)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model, quantization_config=quant,
        torch_dtype=torch.bfloat16, device_map="auto", trust_remote_code=False)
    peft_config = None
    if args.sft_checkpoint and Path(args.sft_checkpoint).exists():
        print(f"[grpo] starting from adapter {args.sft_checkpoint}")
        model = PeftModel.from_pretrained(model, str(args.sft_checkpoint), is_trainable=True)
    else:
        peft_config = LoraConfig(r=32, lora_alpha=64, lora_dropout=0.05, bias="none",
                                 task_type="CAUSAL_LM",
                                 target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                                                 "gate_proj", "up_proj", "down_proj"])

    cfg = GRPOConfig(
        output_dir=str(args.output_dir),
        num_generations=args.num_generations,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=2,
        learning_rate=args.lr,
        beta=args.beta,
        num_train_epochs=args.epochs,
        max_prompt_length=args.max_prompt_len,
        max_completion_length=args.max_completion_len,
        bf16=True,
        logging_steps=5,
        save_strategy="epoch",
        gradient_checkpointing=True,
        report_to=[],
    )
    trainer = GRPOTrainer(
        model=model,
        reward_funcs=[make_reward_fn()],
        args=cfg,
        train_dataset=ds,
        processing_class=tok,
        **({"peft_config": peft_config} if peft_config is not None else {}),
    )
    trainer.train()
    trainer.save_model(str(args.output_dir))
    tok.save_pretrained(str(args.output_dir))
    print(f"[grpo] saved GRPO adapter -> {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
