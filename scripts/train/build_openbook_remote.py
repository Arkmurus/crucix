"""build_openbook_remote — Stage-1 Lane-A clear #2 (R-F1641).

build_openbook_eval.py MUST run where the ChromaDB store lives (aria-intel
/data/aria_rag). Run locally, the store is empty → every question gets blank
context (the 0/689 that blocked grounded training). This builder removes that
constraint: it retrieves each question's evidence from the LIVE store via the
read-only `/api/aria/rag/search` endpoint and formats it into the same
`context` field build_grounded_corpus.py consumes — so the corpus can be built
off-box, on any machine, with zero memory load on the single brain machine.

Retrieval is read-only and uses the already-warm production service, so it adds
no embedder/ChromaDB process to the single aria-intel machine (unlike an on-box
script run). Context formatting mirrors rag_store._format_rag_context so the
training distribution matches what the model sees at serve time.

Usage:
  python scripts/train/build_openbook_remote.py \
    --in data/training/aria_train_689_questions.jsonl \
    --out data/training/aria_train_689_openbook.jsonl \
    [--base https://aria-intel.fly.dev] [--k 6] [--max-chars 6000] [--limit 0]

Token: --token, else $ARIA_INTERNAL_TOKEN / $INTERNAL_TOKEN, else the first
`*TOKEN=` line in .env (gitignored).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def _load_token(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    for k in ("ARIA_INTERNAL_TOKEN", "INTERNAL_TOKEN"):
        if os.environ.get(k):
            return os.environ[k]
    env = Path(".env")
    if env.exists():
        for ln in env.read_text(encoding="utf-8").splitlines():
            if "TOKEN=" in ln and not ln.strip().startswith("#"):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _format_context(results: list[dict], max_chars: int) -> str:
    """Mirror rag_store._format_rag_context: a labelled evidence block with an
    inline source on each chunk so the grounded-corpus citation gate can fire."""
    if not results:
        return ""
    lines = [
        "[RAG RETRIEVED — proprietary intelligence indexed from your sources. "
        "Cite each fact inline using its [Source: ...] label.]"
    ]
    total = 0
    for r in results:
        text = (r.get("text") or "").strip()
        if not text:
            continue
        src = r.get("source") or r.get("title") or r.get("url") or "unknown"
        chunk = f"- {text} [Source: {src}]"
        if total + len(chunk) > max_chars:
            break
        lines.append(chunk)
        total += len(chunk)
    # only the header => no real evidence
    return "\n".join(lines) if len(lines) > 1 else ""


async def _search(client, base: str, token: str, query: str, k: int) -> list[dict]:
    import httpx
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        resp = await client.post(
            f"{base}/api/aria/rag/search",
            headers=headers, json={"query": query, "k": k}, timeout=30.0,
        )
        if resp.status_code != 200:
            return [{"_error": f"HTTP {resp.status_code}"}]
        return resp.json().get("results") or []
    except Exception as e:  # network/timeout — surfaced, never silently empty
        return [{"_error": str(e)[:160]}]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="inp", type=Path, required=True)
    ap.add_argument("--out", dest="out", type=Path, required=True)
    ap.add_argument("--base", default="https://aria-intel.fly.dev")
    ap.add_argument("--k", type=int, default=6)
    ap.add_argument("--max-chars", type=int, default=6000)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--token", default=None)
    args = ap.parse_args()

    token = _load_token(args.token)
    if not token:
        print("FATAL: no token (--token / $ARIA_INTERNAL_TOKEN / .env)", file=sys.stderr)
        return 1

    rows = [json.loads(ln) for ln in args.inp.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if args.limit:
        rows = rows[: args.limit]

    import httpx
    sem = asyncio.Semaphore(max(1, args.concurrency))
    n_ctx = n_empty = n_err = 0
    out: list[dict] = [None] * len(rows)  # type: ignore

    async with httpx.AsyncClient() as client:
        async def one(i: int, r: dict) -> None:
            nonlocal n_ctx, n_empty, n_err
            q = (r.get("question") or "").strip()
            async with sem:
                results = await _search(client, args.base, token, q, args.k)
            if results and isinstance(results[0], dict) and results[0].get("_error"):
                n_err += 1
                print(f"  [{i}] retrieval error: {results[0]['_error']}", file=sys.stderr)
                results = []
            ctx = _format_context(results, args.max_chars)
            rec = dict(r)
            if ctx:
                rec["context"] = ctx
                rec["_context_sources"] = len(results)
                n_ctx += 1
            else:
                n_empty += 1
            out[i] = rec
            done = n_ctx + n_empty + n_err
            if done % 50 == 0:
                print(f"  {done}/{len(rows)} — {n_ctx} grounded, {n_empty} empty, {n_err} err")

        await asyncio.gather(*[one(i, r) for i, r in enumerate(rows)])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in out if r is not None) + "\n",
        encoding="utf-8", newline="\n")
    print(f"wrote {len([r for r in out if r])} rows -> {args.out}  "
          f"({n_ctx} with context, {n_empty} empty, {n_err} errors)")
    if n_ctx == 0:
        print("WARNING: 0 questions got context — check the token / base / store health.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
