"""R-F2539 — OBJECTIVE citation-precision eval: sovereign vs DeepSeek.

The DeepSeek-judge eval (eval_aria_llm.py) is stylistically biased toward DeepSeek
and does NOT measure the USP — accurate citation / low fabrication on grounded
synthesis. This harness scores BOTH models with the OBJECTIVE, deterministic
grounding_reward (no LLM judge): for each (question, context) it generates an
answer that must cite ONLY from the context, then scores citation_precision,
fabricated-citation count, keyword_recall, and the composite reward.

Checkpoint-resumable (append-only results JSONL keyed by seed_id) so a killed run
resumes without re-generating. Run with --report to aggregate the head-to-head.

Usage:
  # generate + score (resumable):
  python scripts/eval/objective_citation_eval.py \
    --eval-set data/eval_reports/aria_eval_500q_openbook.jsonl \
    --sov-url https://<pod>-8888.proxy.runpod.net/v1 --sov-model aria-llm-v0.1 \
    --out data/eval_reports/objective_citation_<tag>.jsonl \
    --limit 120 --concurrency 6
  # aggregate:
  python scripts/eval/objective_citation_eval.py --report --out data/eval_reports/objective_citation_<tag>.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aria_service.intel import grounding_reward as gr  # noqa: E402

_PROMPT = (
    "[CONTEXT — answer ONLY from this evidence; cite each fact inline using its "
    "[Source: ...] label; if the context does not contain the answer, say so]\n"
    "{context}\n\nQuestion: {question}\n\nAnswer (grounded, cited):"
)


def _kw(row) -> list:
    k = row.get("expected_keywords")
    if isinstance(k, str):
        try:
            k = json.loads(k)
        except Exception:
            k = [t.strip() for t in k.strip("[]").replace("'", "").split(",") if t.strip()]
    return k if isinstance(k, list) else []


def _answerable(row) -> bool | None:
    ea = (row.get("expected_answer") or "").lower()
    if not ea:
        return None
    # honest-abstention golden answers => unanswerable; a substantive golden => answerable
    abstain_markers = ("cannot give", "cannot confirm", "without a recent", "do not have",
                       "no confirmed", "insufficient", "not contain", "unable to")
    return not any(m in ea for m in abstain_markers)


async def _gen(client, url, model, key, question, context, *, grounded: bool = True) -> str:
    # grounded=True  -> ARIA-platform reasoning: cite ONLY from the retrieved context, abstain if absent.
    # grounded=False -> RAW frontier baseline: the bare question, no evidence (what a client gets direct).
    if grounded:
        content = _PROMPT.format(context=(context or "")[:9000], question=question)
    else:
        content = question
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    r = await client.post(
        f"{url.rstrip('/')}/chat/completions", headers=headers,
        json={"model": model, "messages": [{"role": "user", "content": content}],
              "max_tokens": 500, "temperature": 0.2},
    )
    r.raise_for_status()
    return (r.json()["choices"][0]["message"]["content"] or "").strip()


def _score(text, context, kws, answerable) -> dict:
    b = gr.score(text or "", context or "", expected_keywords=kws, answerable=answerable)
    d = b.as_dict() if hasattr(b, "as_dict") else {}
    return {
        "score": round(float(getattr(b, "score", 0.0)), 4),
        "citation_precision": round(float(d.get("citation_precision", 0.0)), 4),
        "fabricated_citations": int(d.get("fabricated_citations", 0)),
        "keyword_recall": round(float(d.get("keyword_recall", 0.0)), 4),
        "total_citations": int(d.get("total_citations", 0) or 0),
    }


async def run(args) -> int:
    import httpx

    rows = [json.loads(l) for l in open(args.eval_set, encoding="utf-8") if l.strip()]
    if args.limit:
        rows = rows[: args.limit]
    out = Path(args.out)
    done = set()
    if out.exists():
        for l in open(out, encoding="utf-8"):
            try:
                done.add(json.loads(l)["seed_id"])
            except Exception:
                pass
    todo = [r for r in rows if r.get("seed_id") not in done]
    print(f"[obj-eval] {len(rows)} rows, {len(done)} already scored, {len(todo)} to do "
          f"(concurrency {args.concurrency})")
    dsk = os.getenv("DEEPSEEK_API_KEY", "")
    sem = asyncio.Semaphore(args.concurrency)
    lock = asyncio.Lock()
    n_ok = [0]

    async def one(row):
        q, ctx = row.get("question", ""), row.get("context", "")
        kws, ans = _kw(row), _answerable(row)
        async with sem:
            try:
                async with httpx.AsyncClient(timeout=90.0) as c:
                    if args.platform_eval:
                        # PLATFORM = frontier model grounded in ARIA's retrieved evidence (cite/abstain).
                        # PLATFORM_VERIFIED = platform answer after R-F2540 citation verification.
                        # RAW = same frontier model, bare question, no evidence (client going direct).
                        from aria_service.intel import citation_verifier as _cv
                        plat = await _gen(c, "https://api.deepseek.com/v1", "deepseek-chat", dsk, q, ctx, grounded=True)
                        raw = await _gen(c, "https://api.deepseek.com/v1", "deepseek-chat", dsk, q, ctx, grounded=False)
                        plat_clean = _cv.verify_and_clean(plat, ctx)["answer"]
                        rec = {"seed_id": row.get("seed_id"), "topic": row.get("topic"), "answerable": ans,
                               "platform": _score(plat, ctx, kws, ans),
                               "platform_verified": _score(plat_clean, ctx, kws, ans),
                               "raw": _score(raw, ctx, kws, ans)}
                    else:
                        sov = await _gen(c, args.sov_url, args.sov_model, args.sov_key or None, q, ctx)
                        ds = await _gen(c, "https://api.deepseek.com/v1", "deepseek-chat", dsk, q, ctx) if dsk else ""
                        rec = {"seed_id": row.get("seed_id"), "topic": row.get("topic"), "answerable": ans,
                               "sovereign": _score(sov, ctx, kws, ans),
                               "deepseek": _score(ds, ctx, kws, ans) if dsk else None}
            except Exception as e:
                print(f"  skip {row.get('seed_id')}: {type(e).__name__}", file=sys.stderr)
                return
            async with lock:
                with out.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_ok[0] += 1
                if n_ok[0] % 10 == 0:
                    print(f"  scored {n_ok[0]}/{len(todo)}")

    await asyncio.gather(*[one(r) for r in todo])
    print(f"[obj-eval] done — {n_ok[0]} newly scored → {out}")
    return 0


def report(args) -> int:
    recs = [json.loads(l) for l in open(args.out, encoding="utf-8") if l.strip()]
    if not recs:
        print("no records"); return 1
    order = ["platform", "platform_verified", "raw", "sovereign", "deepseek"]
    sides = [s for s in order if any(r.get(s) for r in recs)]

    def agg(side):
        vals = [r[side] for r in recs if r.get(side)]
        n = len(vals) or 1
        return {
            "citation_precision": round(sum(v["citation_precision"] for v in vals) / n, 4),
            "mean_fabricated": round(sum(v["fabricated_citations"] for v in vals) / n, 4),
            "keyword_recall": round(sum(v["keyword_recall"] for v in vals) / n, 4),
            "mean_reward": round(sum(v["score"] for v in vals) / n, 4),
            "pct_zero_fabrication": round(sum(1 for v in vals if v["fabricated_citations"] == 0) / n, 4),
        }
    aggs = {s: agg(s) for s in sides}
    print(f"=== OBJECTIVE EVAL (grounding_reward) — {len(recs)} rows ===")
    print(f"{'metric':<22}" + "".join(f"{s.upper():>19}" for s in sides))
    for k in ("citation_precision", "mean_fabricated", "keyword_recall", "mean_reward", "pct_zero_fabrication"):
        print(f"{k:<22}" + "".join(f"{aggs[s][k]:>19}" for s in sides))
    if "platform" in sides and "platform_verified" in sides:
        p, v = aggs["platform"], aggs["platform_verified"]
        print(f"\n>>> CITATION-VERIFIER EFFECT (R-F2540):")
        print(f"    mean fabricated citations : {p['mean_fabricated']}  ->  {v['mean_fabricated']}")
        print(f"    fabrication-free answers  : {round(p['pct_zero_fabrication']*100,1)}%  ->  "
              f"{round(v['pct_zero_fabrication']*100,1)}%")
    if len(sides) >= 2:
        a, b = sides[0], sides[1]
        both = [r for r in recs if r.get(a) and r.get(b)]
        aw = sum(1 for r in both if r[a]["score"] > r[b]["score"])
        ties = sum(1 for r in both if r[a]["score"] == r[b]["score"])
        if both:
            print(f"\nhead-to-head {a} vs {b} (n={len(both)}): {a}>{b} {aw} | ties {ties} | "
                  f"{b}>{a} {len(both)-aw-ties}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eval-set", default="data/eval_reports/aria_eval_500q_openbook.jsonl")
    ap.add_argument("--sov-url"); ap.add_argument("--sov-model", default="aria-llm-v0.1")
    ap.add_argument("--sov-key", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--platform-eval", action="store_true",
                    help="PLATFORM (DeepSeek grounded in ARIA evidence) vs RAW DeepSeek; no sovereign endpoint needed")
    args = ap.parse_args()
    if args.report:
        return report(args)
    if not args.platform_eval and not args.sov_url:
        print("--sov-url required (or use --platform-eval)", file=sys.stderr); return 2
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
