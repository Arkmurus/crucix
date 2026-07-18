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
import os
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
    # R-F2586 — GRPO cycle 3 precision lever. Env-configurable precision/recall
    # split; default 0.5 = v2 behaviour. Cycle 3 set it 0.75 (which de-weighted
    # recall and drove over-abstention); cycle 4 restores 0.5 and uses the coverage
    # lever below instead.
    def _envf(name, default):
        try:
            return float(os.environ.get(name, str(default)))
        except Exception:
            return default
    _prec_w = _envf("GROUNDING_PRECISION_WEIGHT", 0.5)
    # Cycle-4 COVERAGE lever — reward grounded-citation breadth on answerable rows to
    # close the confirmed v3 coverage gap (22% of answerable rows uncited). Defaults
    # (0.0 / 2.0 / 0.1) = backward-compatible. Cycle 4 sets GROUNDING_COVERAGE_WEIGHT
    # (~0.15). Double-gated in score() so it can never reward fabrication.
    _cov_w = _envf("GROUNDING_COVERAGE_WEIGHT", 0.0)
    _cov_tgt = _envf("GROUNDING_COVERAGE_TARGET", 2.0)
    _nocite_pen = _envf("GROUNDING_ANSWERABLE_NOCITE_PENALTY", 0.1)
    print(f"[grpo] reward: precision_weight={_prec_w} coverage_weight={_cov_w} "
          f"coverage_target={_cov_tgt} answerable_nocite_penalty={_nocite_pen}")

    def grounding_reward_fn(prompts=None, completions=None, **kwargs):
        contexts = kwargs.get("context")
        answerables = kwargs.get("answerable")   # R-F2033 — per-prompt answerability
        keywords = kwargs.get("expected_keywords")  # R-F2558 — recall/substance signal
        comps = completions or []
        if contexts is None:
            # Fall back to the prompt text as context (it carries the [Source:]
            # markers) so the reward can still verify citations.
            contexts = [_prompt_text(p) for p in (prompts or [])]
        out = []
        for i, c in enumerate(comps):
            ctx = contexts[i] if i < len(contexts) else ""
            # R-F2558: coerce answerable to a REAL bool — the dataset historically
            # carried it as the string "True"/"False", which silently defeated
            # score()'s `is True`/`is False` checks (dead abstention + over-claim
            # logic in cycle #1). Never trust the raw type again.
            ans = _to_bool(answerables[i]) if (answerables is not None and i < len(answerables)) else None
            kw = keywords[i] if (keywords is not None and i < len(keywords)) else None
            out.append(float(gr.reward(_completion_text(c), ctx or "",
                                       answerable=ans, expected_keywords=kw,
                                       precision_weight=_prec_w,
                                       coverage_weight=_cov_w,
                                       coverage_target=_cov_tgt,
                                       answerable_nocite_penalty=_nocite_pen)))
        return out
    grounding_reward_fn.__name__ = "grounding_reward"
    return grounding_reward_fn


def _to_bool(v):
    """R-F2558 — robust answerable coercion. Historically the dataset stored
    answerable as the STRING "True"/"False"; `"False"` is truthy, so every
    identity/`if` check silently misfired. Normalise once, here."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    if v is None:
        return None
    return bool(v)


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
    n_ans = sum(1 for r in rows if _to_bool(r.get("answerable")))
    n_kw = sum(1 for r in rows if r.get("expected_keywords"))
    print(f"[dry-run] dataset OK: {len(rows)} prompts (answerable={n_ans}, "
          f"unanswerable={len(rows) - n_ans}, with expected_keywords={n_kw}), "
          f"all have prompt+context+answerable")

    # R-F2762 — BULLETPROOF the §24 pre-flight. A recall-WEIGHTED cycle
    # (GROUNDING_PRECISION_WEIGHT < 1.0 — the default is 0.5, so recall carries
    # (1-pw) of the answering-row score) trained on a dataset with (near-)zero
    # expected_keywords silently DROPS the recall objective: the reward's recall
    # term is 0 on every row, so the run CLAIMS to optimise recall but cannot. Before
    # this guard, dry_run() merely SKIPPED the recall assertion (the `if kw_row`
    # block below) and still printed PASS — green-lighting exactly such a no-op cycle
    # (live footgun: run_grpo_cycle.sh defaulted to a keyword-less prompt file). §24
    # "training must be REAL" + the honesty mandate ("ARIA is 100% what she says she
    # is") → FAIL, don't skip. An intentional precision-only cycle is exempt:
    # set GROUNDING_PRECISION_WEIGHT=1.0 (recall weight 0).
    _pw = float(os.environ.get("GROUNDING_PRECISION_WEIGHT", "0.5") or 0.5)
    _kw_floor = float(os.environ.get("GROUNDING_MIN_KEYWORD_COVERAGE", "0.5") or 0.5)
    _kw_cov = (n_kw / n_ans) if n_ans else 0.0
    if _pw < 1.0 and _kw_cov < _kw_floor:
        raise AssertionError(
            f"R-F2762: recall-weighted cycle (GROUNDING_PRECISION_WEIGHT={_pw} < 1.0) "
            f"but only {n_kw}/{n_ans} answerable rows carry expected_keywords "
            f"({_kw_cov:.0%} < {_kw_floor:.0%} floor) — the recall reward term would be "
            f"DEAD, so the cycle would silently NO-OP its recall objective. Use a "
            f"keyworded dataset (e.g. data/training/grpo_grounded_prompts_v2.jsonl) or "
            f"set GROUNDING_PRECISION_WEIGHT=1.0 for an intentional precision-only cycle.")

    rf = make_reward_fn()
    ans_row = next((r for r in rows if _to_bool(r.get("answerable"))), rows[0])
    unans_row = next((r for r in rows if _to_bool(r.get("answerable")) is False), None)
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

    # R-F2558: the RECALL signal must be LIVE — a grounded answer that STATES the
    # expected facts must beat an equally-grounded but terse answer that omits them.
    # (In cycle #1 expected_keywords was absent, so this term was always 0 and the
    # model was never pushed to be substantive → recall stayed flat at ~0.22.)
    kw_row = next((r for r in rows if _to_bool(r.get("answerable")) and r.get("expected_keywords")), None)
    if kw_row is not None:
        kctx = kw_row["context"]; kws = kw_row["expected_keywords"]
        ksrc = (gr.extract_citations(kctx) or ["web_search:example"])[0]
        substantive = f"The evidence states {', '.join(kws)} [Source: {ksrc}]."
        terse = f"Per the evidence, the answer is noted [Source: {ksrc}]."
        r_sub, r_terse = rf(completions=[substantive, terse],
                            context=[kctx, kctx], answerable=[True, True],
                            expected_keywords=[kws, kws])
        print(f"[dry-run] RECALL: substantive={r_sub:.3f}  terse={r_terse:.3f}  (kw n={len(kws)})")
        assert r_sub > r_terse, (f"R-F2558 recall signal DEAD: stating the facts ({r_sub}) "
                                 f"must beat terse ({r_terse}) — expected_keywords not wired")

    # Cycle-4 COVERAGE lever pre-flight: when GROUNDING_COVERAGE_WEIGHT>0, citing MORE
    # grounded facts must score higher (closes the answerable-row coverage gap), a
    # 0-citation answerable answer must lose to a grounded one, and fabrication must
    # still not pay. Only runs when the lever is enabled so the default pipeline is
    # unchanged.
    if float(os.environ.get("GROUNDING_COVERAGE_WEIGHT", "0") or 0) > 0:
        two_src = gr.extract_citations(ctx)
        if len(two_src) >= 2:
            one = f"Fact A [Source: {two_src[0]}]."
            two = f"Fact A [Source: {two_src[0]}]. Fact B [Source: {two_src[1]}]."
            none = "Fact A is the case, but no source is given."
            r_one, r_two, r_none = rf(completions=[one, two, none],
                                      context=[ctx, ctx, ctx], answerable=[True, True, True])
            print(f"[dry-run] COVERAGE: 2-cite={r_two:.3f}  1-cite={r_one:.3f}  0-cite={r_none:.3f}")
            assert r_two > r_one, f"coverage lever DEAD: 2 grounded cites ({r_two}) must beat 1 ({r_one})"
            assert r_one > r_none, f"0-citation answerable ({r_none}) must lose to a grounded answer ({r_one})"
            assert r_none >= r_f, f"honest silence ({r_none}) must not score below fabrication ({r_f})"

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
    # R-F2033 Phase 3 — vLLM-accelerated generation. GRPO spends ~all its time
    # generating G completions/prompt; HF generate on a 4-bit 7B was ~12 min/step
    # (full run ~11h). vLLM (colocate: a second, bf16 copy of the policy on the same
    # GPU, LoRA-synced each step) is 10-50x faster. Requires bf16 (NOT 4-bit) + a
    # roomy GPU (A100-80) since vLLM holds its own weights + KV cache alongside training.
    ap.add_argument("--use-vllm", action="store_true",
                    help="vLLM colocate generation (needs bf16 + A100-80; not with --load-in-4bit)")
    ap.add_argument("--vllm-gpu-mem", type=float, default=0.3,
                    help="fraction of GPU mem reserved for the vLLM engine (rest = training)")
    ap.add_argument("--dry-run", action="store_true", help="validate dataset+reward, NO GPU/trl")
    args = ap.parse_args()
    if args.use_vllm and args.load_in_4bit:
        print("[grpo] WARNING: --use-vllm needs bf16 weights; ignoring --load-in-4bit.")
        args.load_in_4bit = False

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

    cfg_kwargs = dict(
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
        # R-F2558: PERIODIC checkpoints, not epoch-only. A prior v2 run was hard-
        # stopped at step 109/110 by a racy self-stop watcher and lost EVERYTHING
        # because save_strategy="epoch" only writes at the end. Step checkpoints make
        # a late kill cost <=SAVE_STEPS steps and let the re-run resume.
        save_strategy="steps",
        save_steps=int(os.environ.get("SAVE_STEPS", "20")),
        save_total_limit=int(os.environ.get("SAVE_TOTAL_LIMIT", "3")),
        gradient_checkpointing=True,
        report_to=[],
    )
    if args.use_vllm:
        # colocate: vLLM shares the training GPU (single-pod). The smoke gate
        # exercises this exact path so a trl/vLLM version mismatch aborts cheaply.
        cfg_kwargs.update(
            use_vllm=True,
            vllm_mode="colocate",
            vllm_gpu_memory_utilization=args.vllm_gpu_mem,
        )
        print(f"[grpo] vLLM colocate ON (gpu_mem={args.vllm_gpu_mem}) — fast generation")
    cfg = GRPOConfig(**cfg_kwargs)
    trainer = GRPOTrainer(
        model=model,
        reward_funcs=[make_reward_fn()],
        args=cfg,
        train_dataset=ds,
        processing_class=tok,
        **({"peft_config": peft_config} if peft_config is not None else {}),
    )
    # R-F2558: resume from the latest step-checkpoint if one exists in output_dir
    # (True lets TRL/Trainer auto-find it), so a killed run continues where it left off.
    _resume = any(Path(args.output_dir).glob("checkpoint-*")) if Path(args.output_dir).exists() else False
    if _resume:
        print(f"[grpo] resuming from latest checkpoint in {args.output_dir}")
    trainer.train(resume_from_checkpoint=_resume)
    trainer.save_model(str(args.output_dir))
    tok.save_pretrained(str(args.output_dir))
    print(f"[grpo] saved GRPO adapter -> {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
