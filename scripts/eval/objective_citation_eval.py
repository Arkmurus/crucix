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

# R-F2790 — the ENFORCED Cycle-6 promotion gate (was a manual eyeball decision, which
# is exactly where a false "it's better" slips through — the opposite of ARIA's USP).
# A checkpoint is promoted ONLY if it clears every honesty+precision floor AND beats
# the SAME-RUN DeepSeek baseline on precision AND recall. Single source of truth so the
# thresholds cannot drift between the eval and whoever reads it.
_GATE = {
    "precision_floor": 0.750,        # citation_precision absolute floor (the moat)
    "recall_floor": 0.238,           # raw keyword_recall absolute floor
    "grounded_recall_floor": 0.40,   # R-F2805 — grounded_recall min (per-row avg incl. 0-extractable rows ~0.45; the binding target is >= same-run DeepSeek)
    "fabrication_ceiling": 0.18,     # mean fabricated citations per answer, upper bound
    "zero_fab_floor": 0.847,         # fraction of answers with ZERO fabricated citations
}

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
    # R-F2805 (cycle 7) — grounded_recall = recall over ONLY the keywords that are in
    # the context (extractable). On this eval ~75% of gold keywords are ungrounded
    # domain vocab, so raw keyword_recall rewards fabrication; grounded_recall is the
    # honest, USP-aligned recall metric. Reported alongside raw for comparability.
    _gk = gr._grounded_keywords(kws, context)
    return {
        "score": round(float(getattr(b, "score", 0.0)), 4),
        "citation_precision": round(float(d.get("citation_precision", 0.0)), 4),
        "fabricated_citations": int(d.get("fabricated_citations", 0)),
        "keyword_recall": round(float(d.get("keyword_recall", 0.0)), 4),
        "grounded_recall": round(float(gr._keyword_recall(text or "", _gk)), 4),
        "grounded_kw_count": len(_gk),
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
                        # Cycle-4 diagnosis gap: metrics-only records made root-causing the
                        # v3 coverage regression impossible (could not re-score or inspect the
                        # uncited rows). --store-text persists the generated answers + question
                        # (+ truncated context) so a later cycle can re-score offline. Default
                        # OFF so existing reports stay byte-identical (report() ignores extras).
                        if args.store_text:
                            rec["question"] = q
                            rec["sovereign_text"] = sov
                            rec["deepseek_text"] = ds
                            rec["context"] = (ctx or "")[:4000]
                            rec["expected_keywords"] = kws
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


def _promotion_gate(aggs, use_grounded: bool = False) -> tuple[bool, list[str]]:
    """R-F2790 — fail-closed Cycle-6 promotion verdict from the report aggregates.

    Returns (promote, lines). Promote is True ONLY when the sovereign checkpoint
    clears every criterion below. FAIL-CLOSED: no sovereign side, or no same-run
    DeepSeek baseline (so ">= DeepSeek" cannot be PROVEN), is NO-PROMOTE — ARIA never
    promotes on incomplete evidence, the same 'never a false clean' rule she applies
    to a DD verdict, applied to her own model.
    """
    sov = aggs.get("sovereign")
    ds = aggs.get("deepseek")
    if not sov:
        return False, ["NO-PROMOTE — no `sovereign` aggregates in this report (nothing to gate)."]
    if not ds:
        return False, [
            "NO-PROMOTE — no same-run `deepseek` baseline in this report, so "
            ">= DeepSeek cannot be proven (fail-closed). Re-run the eval with the "
            "DeepSeek side populated before gating."
        ]
    # R-F2805 (cycle 7) — with use_grounded the recall criteria use grounded_recall
    # (honest extraction of in-context substance) instead of raw keyword_recall (which
    # rewards ungrounded domain vocab = fabrication). Everything else is unchanged.
    _rk = "grounded_recall" if use_grounded else "keyword_recall"
    _rfloor = _GATE["grounded_recall_floor"] if use_grounded else _GATE["recall_floor"]
    _rlabel = "grounded_recall" if use_grounded else "recall"
    checks = [
        ("precision >= 0.750 floor",
         sov["citation_precision"] >= _GATE["precision_floor"],
         f"{sov['citation_precision']} vs floor {_GATE['precision_floor']}"),
        ("precision >= DeepSeek (same-run)",
         sov["citation_precision"] >= ds["citation_precision"],
         f"{sov['citation_precision']} vs DeepSeek {ds['citation_precision']}"),
        (f"{_rlabel} >= {_rfloor} floor",
         sov.get(_rk, 0.0) >= _rfloor,
         f"{sov.get(_rk, 0.0)} vs floor {_rfloor}"),
        (f"{_rlabel} >= DeepSeek (same-run)",
         sov.get(_rk, 0.0) >= ds.get(_rk, 0.0),
         f"{sov.get(_rk, 0.0)} vs DeepSeek {ds.get(_rk, 0.0)}"),
        ("mean_fabricated <= 0.18",
         sov["mean_fabricated"] <= _GATE["fabrication_ceiling"],
         f"{sov['mean_fabricated']} vs ceiling {_GATE['fabrication_ceiling']}"),
        ("pct_zero_fabrication >= 0.847",
         sov["pct_zero_fabrication"] >= _GATE["zero_fab_floor"],
         f"{sov['pct_zero_fabrication']} vs floor {_GATE['zero_fab_floor']}"),
    ]
    lines = [f"  [{'PASS' if ok else 'FAIL'}] {name}  ({detail})" for name, ok, detail in checks]
    promote = all(ok for _, ok, _ in checks)
    lines.append(
        f">>> {'PROMOTE' if promote else 'NO-PROMOTE'} — "
        + ("all promotion gates cleared." if promote
           else "at least one gate failed; v1/v4 stays the base (rollback).")
    )
    return promote, lines


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
            # R-F2805 (cycle 7) — the honest recall metric (0.0 on pre-R-F2805 reports
            # that lack per-row grounded_recall; recompute those from stored text if needed).
            "grounded_recall": round(sum(v.get("grounded_recall", 0.0) for v in vals) / n, 4),
            "mean_reward": round(sum(v["score"] for v in vals) / n, 4),
            "pct_zero_fabrication": round(sum(1 for v in vals if v["fabricated_citations"] == 0) / n, 4),
        }
    aggs = {s: agg(s) for s in sides}
    print(f"=== OBJECTIVE EVAL (grounding_reward) — {len(recs)} rows ===")
    print(f"{'metric':<22}" + "".join(f"{s.upper():>19}" for s in sides))
    for k in ("citation_precision", "mean_fabricated", "keyword_recall", "grounded_recall", "mean_reward", "pct_zero_fabrication"):
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
    # R-F2790 — enforced, fail-closed promotion verdict. Backward-compatible: only runs
    # under --gate, and drives the process exit code so an automated cycle CANNOT promote
    # a checkpoint that did not clear every gate (exit 1 on NO-PROMOTE).
    if getattr(args, "gate", False):
        promote, glines = _promotion_gate(aggs, use_grounded=getattr(args, "grounded_recall_gate", False))
        print("\n=== CYCLE-6 PROMOTION GATE (R-F2790 — enforced, fail-closed) ===")
        for line in glines:
            print(line)
        return 0 if promote else 1
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
    ap.add_argument("--gate", action="store_true",
                    help="R-F2790: with --report, apply the enforced Cycle-6 promotion "
                         "gate (sovereign vs same-run DeepSeek) and EXIT 1 on NO-PROMOTE. "
                         "Fail-closed: missing DeepSeek baseline => NO-PROMOTE.")
    ap.add_argument("--grounded-recall-gate", action="store_true",
                    help="R-F2805 (cycle 7): with --gate, use grounded_recall (honest "
                         "extraction of in-context substance) for the recall criteria "
                         "instead of raw keyword_recall (which rewards ungrounded vocab).")
    ap.add_argument("--store-text", action="store_true",
                    help="persist generated answers + question + truncated context per row "
                         "(default off; enables offline re-scoring / coverage-gap diagnosis)")
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
