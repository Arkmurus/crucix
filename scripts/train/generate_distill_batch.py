#!/usr/bin/env python
"""generate_distill_batch — produce a raw SFT distillation batch (R-F1470).

Drives ARIA's R-F1467 DataEnginePipeline with the R-F1469 live DeepSeek
clients to generate a batch of distillation SFT pairs for the v0.3 training
cycle (data/_V03_TRAINING_RUNBOOK.md). Reuses the proven pipeline stages
(question-gen -> intra-batch cosine dedup -> contamination check vs the frozen
500-Q eval set) and adds:

  * cross-batch dedup vs an existing batch (so batch2 does not repeat batch1),
  * bounded-concurrency answer generation (the pipeline's own answer stage is
    sequential; concurrency keeps the paid run to a few minutes, not ~an hour).

Output format MATCHES data/training/aria_sft_distill_batch1.jsonl exactly:
    {"messages":[{"role":"user",...},{"role":"assistant",...}],
     "topic":"...","source":"deepseek_distill_v1"}

This makes the combined 500-pair corpus (batch1 + batch2) byte-uniform and
fed directly to scripts/train/sft_train.py --train-file.

Env: DEEPSEEK_API_KEY (loaded from .env if not already in the environment).
PAID: makes real DeepSeek API calls. ~$<1 for 400 pairs.

Usage:
    python scripts/train/generate_distill_batch.py \
        --n-per-topic 22 --target 400 \
        --exclude data/training/aria_sft_distill_batch1.jsonl \
        --out data/training/aria_sft_distill_batch2.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Repo root on sys.path so `aria_service.*` imports resolve when run directly.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load_dotenv() -> None:
    """Minimal .env loader (no dependency). Does not override real env vars."""
    env_path = _REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _normalize(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _load_existing_questions(paths: list[str]) -> set[str]:
    """Load normalized question text from existing batch JSONL files."""
    seen: set[str] = set()
    for p in paths:
        fp = Path(p)
        if not fp.exists():
            print(f"[generate_distill_batch] exclude file not found: {p}")
            continue
        with fp.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msgs = d.get("messages") or []
                for m in msgs:
                    if m.get("role") == "user":
                        seen.add(_normalize(m.get("content", "")))
                        break
    return seen


async def _run(args: argparse.Namespace) -> int:
    from aria_service.learning.data_engine_generate import (
        DataEnginePipeline,
        GeneratedPair,
    )
    from aria_service.learning.deepseek_clients import (
        DeepSeekAnswerGenerator,
        DeepSeekQuestionGenerator,
    )

    if not (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ARIA_DEEPSEEK_API_KEY")):
        print("BLOCKED: DEEPSEEK_API_KEY not set (checked env + .env)")
        return 2

    exclude_norm = _load_existing_questions(args.exclude or [])
    print(f"[generate_distill_batch] cross-batch dedup seed: {len(exclude_norm)} existing questions")

    pipe = DataEnginePipeline(
        generator=DeepSeekQuestionGenerator(),
        answer_gen=DeepSeekAnswerGenerator(),
    )

    start = time.time()
    topics = pipe.get_topics()
    print(f"[generate_distill_batch] {len(topics)} topics, n_per_topic={args.n_per_topic}")

    # Stage 2: question generation (paid; one call per topic)
    questions = await pipe.generate_questions(topics, args.n_per_topic)
    raw_q = len(questions)

    # Stage 3: intra-batch cosine dedup
    questions = await pipe.dedup_questions(questions)
    after_dedup = len(questions)

    # Stage 4: contamination check vs the frozen 500-Q eval set
    questions = await pipe.check_contamination(questions)
    after_contam = len(questions)

    # Cross-batch dedup vs the exclude files (e.g. batch1)
    if exclude_norm:
        questions = [q for q in questions if _normalize(q.question) not in exclude_norm]
    after_cross = len(questions)

    # Cap the QUESTION count before paying for answers
    if args.target and len(questions) > args.target:
        questions = questions[: args.target]
    to_answer = len(questions)

    print(
        f"[generate_distill_batch] questions: raw={raw_q} dedup={after_dedup} "
        f"contam={after_contam} cross={after_cross} -> answering {to_answer}"
    )
    if not questions:
        print("BLOCKED: no questions survived filtering")
        return 3

    # Answer generation with bounded concurrency
    sem = asyncio.Semaphore(args.concurrency)
    done = {"n": 0}

    async def _answer(q):
        async with sem:
            ans = await pipe.answer_gen.generate_answer(q.question, q.topic)
            done["n"] += 1
            if done["n"] % 25 == 0:
                print(f"[generate_distill_batch] answered {done['n']}/{to_answer}")
            return q, (ans or "").strip()

    results = await asyncio.gather(*[_answer(q) for q in questions])

    pairs = [
        GeneratedPair(question=q.question, chosen_answer=ans, topic=q.topic)
        for q, ans in results
        if ans
    ]
    after_answers = len(pairs)

    # Stage 6: light sanity check (length bounds)
    pairs = pipe.sanity_check(pairs)
    final = len(pairs)

    # Write in batch1's exact format
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for p in pairs:
            f.write(
                json.dumps(
                    {
                        "messages": [
                            {"role": "user", "content": p.question},
                            {"role": "assistant", "content": p.chosen_answer},
                        ],
                        "topic": p.topic,
                        "source": "deepseek_distill_v1",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    dur = time.time() - start
    print(
        f"[generate_distill_batch] DONE: wrote {final} pairs to {out_path} "
        f"(answers={after_answers} after_sanity={final}) in {dur:.0f}s"
    )
    return 0


def main() -> int:
    _load_dotenv()
    ap = argparse.ArgumentParser(description="Generate a raw SFT distillation batch")
    ap.add_argument("--n-per-topic", type=int, default=22,
                    help="questions to request per topic (default 22)")
    ap.add_argument("--target", type=int, default=400,
                    help="cap on pairs to produce (default 400)")
    ap.add_argument("--concurrency", type=int, default=6,
                    help="concurrent answer-gen calls (default 6)")
    ap.add_argument("--exclude", action="append", default=[],
                    help="existing batch JSONL to dedup against (repeatable)")
    ap.add_argument("--out", required=True, help="output JSONL path")
    args = ap.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
