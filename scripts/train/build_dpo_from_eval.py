"""build_dpo_from_eval — DPO preference pairs from the 500-Q eval (R-F1336).

Recipe: for every golden question the current model FAILED, pair the
golden expected answer (chosen) against the model's own fresh output
(rejected). This trains v0.2 to prefer calibrated, on-topic, honest
answers over exactly the failure modes v0.1 exhibits.

Deliberately EXCLUDED: R-F59/R-F80 adversarial-run pairs. The adversarial
grader has a known artifact (2026-05-26: correct refusals scored as
failures), so chosen/rejected labels from those runs may be INVERTED —
DPO on inverted preferences would train the model to leak. Golden-set
pairs only until the grader is fixed.

Inputs:
  --eval-report  JSON from eval_aria_llm.py (dd_eval.results[].passed)
  --eval-set     the 500-Q JSONL (question, expected_keywords, topic, seed_id)
  --target       OpenAI-compatible URL of the CURRENT model (rejected gen)
  --model        model id at the target
  --out          output JSONL {prompt, chosen, rejected, meta}
  --max-pairs    cap (default 600)
  --concurrency  parallel generations (default 8 — vLLM batches well)

Usage:
  python scripts/train/build_dpo_from_eval.py \
    --eval-report data/training/aria_llm_v01_sft_500q.json \
    --eval-set data/training/aria_eval_500q.jsonl \
    --target https://<pod>-8888.proxy.runpod.net/v1 \
    --model aria-llm-v0.1 \
    --out data/training/aria_dpo_v1.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def load_failed_questions(eval_report: Path, eval_set: Path) -> list[dict]:
    """Join failed eval results back to full questions + golden answers.

    The report truncates question text to 200 chars, so the join key is
    question[:200]. expected_answer comes from SEED_ENTRIES via seed_id.
    """
    from aria_service.intel.eval_golden_seed import SEED_ENTRIES

    by_seed = {e.get("seed_id"): e for e in SEED_ENTRIES}
    report = json.loads(eval_report.read_text(encoding="utf-8"))
    results = (report.get("dd_eval") or {}).get("results") or []
    failed_keys = {
        r["question"] for r in results
        if r.get("passed") is False or r.get("error")
    }

    out = []
    with eval_set.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            q = row.get("question", "")
            if q[:200] not in failed_keys:
                continue
            seed = by_seed.get(row.get("seed_id"))
            golden = (seed or {}).get("expected_answer", "")
            if not golden or len(golden) < 40:
                continue  # no usable chosen side
            out.append({
                "question": q,
                "chosen": golden,
                "topic": row.get("topic", "general"),
                "seed_id": row.get("seed_id", ""),
            })
    return out


async def generate_rejected(items: list[dict], *, target: str, model: str,
                            api_key: str | None, concurrency: int) -> list[dict]:
    """Fresh generations from the current model = the rejected halves."""
    import httpx

    sem = asyncio.Semaphore(concurrency)
    out: list[dict] = []

    async def _one(item: dict) -> None:
        async with sem:
            try:
                async with httpx.AsyncClient(timeout=90.0) as client:
                    headers = {"Content-Type": "application/json"}
                    if api_key:
                        headers["Authorization"] = f"Bearer {api_key}"
                    resp = await client.post(
                        f"{target.rstrip('/')}/chat/completions",
                        headers=headers,
                        json={
                            "model": model,
                            "messages": [{"role": "user", "content": item["question"]}],
                            "max_tokens": 700,
                            "temperature": 0.7,  # sample the model's real failure modes
                        },
                    )
                    resp.raise_for_status()
                    text = resp.json()["choices"][0]["message"]["content"].strip()
                if text and text != item["chosen"]:
                    out.append({
                        "prompt": item["question"],
                        "chosen": item["chosen"],
                        "rejected": text,
                        "meta": {"seed_id": item["seed_id"], "topic": item["topic"],
                                 "source": "golden_vs_v01_eval_fail"},
                    })
            except Exception as e:
                print(f"  skip ({type(e).__name__}): {item['question'][:60]}", file=sys.stderr)

    await asyncio.gather(*[_one(i) for i in items])
    return out


async def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eval-report", type=Path, required=True)
    ap.add_argument("--eval-set", type=Path, required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--max-pairs", type=int, default=600)
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args()

    failed = load_failed_questions(args.eval_report, args.eval_set)
    print(f"failed questions with golden answers: {len(failed)}")
    failed = failed[: args.max_pairs]

    pairs = await generate_rejected(
        failed, target=args.target, model=args.model,
        api_key=args.api_key, concurrency=args.concurrency,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for p in pairs:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(json.dumps({"pairs_written": len(pairs), "path": str(args.out)}))
    # No silent caps: report what was dropped
    if len(failed) > len(pairs):
        print(f"note: {len(failed) - len(pairs)} candidates dropped (gen failures / dup outputs)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
