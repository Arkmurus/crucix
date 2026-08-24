"""gen_questions_v06 — generate the BIGGER v0.6 question pool (R-F1646).

Reuses ARIA's proven DataEnginePipeline question stages (generate -> intra-batch
cosine dedup -> contamination-check vs the frozen 500-Q eval) but emits QUESTIONS
ONLY — we do NOT pay for closed-book answers here, because v0.6 answers are
regenerated GROUNDED (from retrieved context) by build_grounded_corpus. Also
excludes the existing v0.5 question pool so v0.6 adds NEW questions on top of the
608 grounded rows already built.

PAID: real DeepSeek calls for question generation (one per topic). ~$1-2.

Usage:
  python scripts/train/gen_questions_v06.py --n-per-topic 80 \
    --exclude data/training/aria_train_689_questions.jsonl \
    --out data/training/aria_train_v06_new_questions.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _normalize(q: str) -> str:
    return " ".join((q or "").lower().split())


def _load_existing(paths: list[str]) -> set[str]:
    seen: set[str] = set()
    for p in paths or []:
        fp = Path(p)
        if not fp.exists():
            continue
        for ln in fp.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            q = rec.get("question")
            if not q and rec.get("messages"):
                q = rec["messages"][0].get("content", "").split("[QUESTION]\n")[-1]
            if q:
                seen.add(_normalize(q))
    return seen


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-per-topic", type=int, default=80)
    ap.add_argument("--target", type=int, default=0, help="cap kept questions (0=all)")
    ap.add_argument("--exclude", action="append", default=[], help="question files to exclude (repeatable)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    if not (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ARIA_DEEPSEEK_API_KEY")):
        for ln in Path(".env").read_text(encoding="utf-8").splitlines():
            if ln.startswith("DEEPSEEK_API_KEY="):
                os.environ["DEEPSEEK_API_KEY"] = ln.split("=", 1)[1].strip().strip('"').strip("'"); break
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("BLOCKED: DEEPSEEK_API_KEY not set (env + .env)", file=sys.stderr); return 2

    from aria_service.learning.data_engine_generate import DataEnginePipeline
    from aria_service.learning.deepseek_clients import (
        DeepSeekAnswerGenerator, DeepSeekQuestionGenerator,
    )

    exclude_norm = _load_existing(args.exclude)
    print(f"[v06] cross-pool dedup seed: {len(exclude_norm)} existing questions")

    pipe = DataEnginePipeline(
        generator=DeepSeekQuestionGenerator(),
        answer_gen=DeepSeekAnswerGenerator(),  # constructed but NOT used (questions only)
    )
    topics = pipe.get_topics()
    print(f"[v06] {len(topics)} topics, n_per_topic={args.n_per_topic}")

    questions = await pipe.generate_questions(topics, args.n_per_topic)
    raw_q = len(questions)
    questions = await pipe.dedup_questions(questions)
    after_dedup = len(questions)
    questions = await pipe.check_contamination(questions)   # vs frozen 500-Q eval
    after_contam = len(questions)
    if exclude_norm:
        questions = [q for q in questions if _normalize(q.question) not in exclude_norm]
    after_cross = len(questions)
    if args.target and len(questions) > args.target:
        questions = questions[: args.target]

    print(f"[v06] questions: raw={raw_q} dedup={after_dedup} contam_ok={after_contam} "
          f"cross_ok={after_cross} kept={len(questions)}")
    if not questions:
        print("BLOCKED: no questions survived filtering", file=sys.stderr); return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "\n".join(json.dumps({"question": q.question, "topic": q.topic,
                              "source": "v06_grounded_distill"}, ensure_ascii=False)
                  for q in questions) + "\n",
        encoding="utf-8", newline="\n")
    print(f"[v06] wrote {len(questions)} NEW questions -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
