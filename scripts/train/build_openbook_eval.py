"""build_openbook_eval — turn the closed-book 500-Q eval into the OPEN-BOOK
Stage-0 measurement (R-F1533).

For every eval question, retrieve ARIA's REAL evidence via rag_store (the same
7-layer-stack primitive production uses, `get_rag_context_with_sources`) and write
it into a `context` field. eval_aria_llm.py then prepends that context so the model
answers FROM the evidence — measuring the grounded ceiling vs the closed-book 0.288.

MUST RUN where the RAG store actually lives (aria-intel: /data/aria_rag chromadb).
Locally the store is empty, so every question would get blank context — that's the
whole point of running it on the server / coordinating with ARIA.

Usage (on the box that holds the RAG store):
  python scripts/train/build_openbook_eval.py \
    --in data/eval_reports/aria_eval_500q.jsonl \
    --out data/eval_reports/aria_eval_500q_openbook.jsonl
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="inp", type=Path, required=True)
    ap.add_argument("--out", dest="out", type=Path, required=True)
    ap.add_argument("--max-chars", type=int, default=6000, help="cap per-question context")
    args = ap.parse_args()

    from aria_service.intel import rag_store  # heavy deps — only import on the RAG box

    rows = [json.loads(ln) for ln in args.inp.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out, n_ctx, n_empty = [], 0, 0
    for i, r in enumerate(rows):
        q = r.get("question", "")
        try:
            text, sources = await rag_store.get_rag_context_with_sources(q, max_chars=args.max_chars)
        except Exception as e:
            text, sources = "", []
            print(f"  [{i}] retrieval error: {e}", file=sys.stderr)
        if text and text.strip():
            r["context"] = text
            r["_context_sources"] = len(sources or [])
            n_ctx += 1
        else:
            n_empty += 1  # left closed-book — model should ABSTAIN here (honest behaviour)
        out.append(r)
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(rows)} — {n_ctx} grounded, {n_empty} empty")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(out)} rows -> {args.out}  ({n_ctx} with retrieved context, {n_empty} empty)")
    if n_ctx == 0:
        print("WARNING: 0 questions got context — are you running where the RAG store lives?", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
