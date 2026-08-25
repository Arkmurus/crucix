"""rescore_openbook_offline — re-grade a stored eval run under the R-F4332
grounding fix, WITHOUT re-running the model (R-F4333).

WHY THIS IS CHEAP AND EXACT. R-F1459 persists `actual_answer` on every result
precisely so a run can be re-scored offline, so no GPU and no target inference
is needed. And R-F4332 changes the rubric for exactly ONE of the three
grounding modes:

    grounded    context genuinely supports the question -> strict rubric.
                UNCHANGED (the old `bool(context)` was already True here).
    ungrounded  no context at all -> closed-book rubric.
                UNCHANGED (the old boolean was already False here).
    unsupported context present but supports nothing -> strict rubric must NOT
                apply. CHANGED — this is the only bucket that moves.

So only the `unsupported` rows are re-judged. Re-judging all 500 would cost ~3x
more and could only reproduce the other two buckets' existing verdicts — and
would introduce judge nondeterminism into rows the fix does not touch, making
the delta unattributable.

Usage:
  python scripts/train/rescore_openbook_offline.py \
      --run   data/eval_reports/aria_llm_v0_7_grounded_eval.json \
      --eval  data/eval_reports/aria_eval_500q_openbook.jsonl \
      --out   data/eval_reports/aria_llm_v0_7_rescored.json \
      [--limit N]   # pilot on N rows first; prints projected cost
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
if str(_REPO / "scripts" / "train") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts" / "train"))

import eval_aria_llm as E  # noqa: E402


def _load_env() -> None:
    """Read .env without exporting it — the key is used, never printed."""
    f = _REPO / ".env"
    if not f.is_file():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _index_eval(rows: list[dict]) -> dict:
    """Map a question PREFIX to its eval row.

    Result rows store `question[:200]` (eval_aria_llm), so the stored question
    is a prefix of the real one and cannot be matched by equality.
    """
    return {(r.get("question") or "")[:200]: r for r in rows}


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--eval", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0,
                    help="pilot: re-judge only N unsupported rows")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()
    _load_env()

    judge_key = os.getenv("DEEPSEEK_API_KEY")
    if not judge_key:
        print("FATAL: DEEPSEEK_API_KEY not set — the judge cannot run", file=sys.stderr)
        return 2

    run = json.loads(args.run.read_text(encoding="utf-8"))
    dd = run.get("defence_dd") or {}
    results = dd.get("results") or []
    ev_rows = [json.loads(ln) for ln in
               args.eval.read_text(encoding="utf-8").splitlines() if ln.strip()]
    ev_by_q = _index_eval(ev_rows)

    # Classify every stored result; only 'unsupported' is re-judged.
    buckets: dict[str, list] = {"grounded": [], "ungrounded": [], "unsupported": []}
    unmatched = 0
    for r in results:
        q = (r.get("question") or "")[:200]
        ev = ev_by_q.get(q)
        if ev is None:
            unmatched += 1
            r["grounding"] = "unmatched"
            buckets.setdefault("unmatched", []).append(r)
            continue
        mode = E.grounding_mode(ev.get("context"), ev.get("expected_keywords"))
        cov = E.evidence_coverage(ev.get("context"), ev.get("expected_keywords"))
        r["grounding"] = mode
        r["evidence_coverage"] = round(cov, 3) if cov is not None else None
        r["_ctx"] = ev.get("context") or ""
        r["_kw"] = ev.get("expected_keywords") or []
        buckets[mode].append(r)

    todo = buckets["unsupported"]
    if args.limit:
        todo = todo[:args.limit]
    print(f"stored results   : {len(results)}  (unmatched to eval set: {unmatched})")
    for m in ("grounded", "ungrounded", "unsupported"):
        b = buckets[m]
        p = sum(1 for r in b if r.get("passed"))
        print(f"  {m:12s} {len(b):3d}  stored score {p}/{len(b)}"
              + (f" = {p/len(b):.3f}" if b else ""))
    print(f"\nre-judging {len(todo)} 'unsupported' rows "
          f"(the ONLY bucket R-F4332 changes)\n")

    sem = asyncio.Semaphore(args.concurrency)
    changed = {"correct": 0, "partial": 0, "wrong": 0, "unscored": 0}
    flipped = 0

    async def one(r: dict) -> None:
        nonlocal flipped
        async with sem:
            jr = await E._judge_answer(
                # BASE only — _judge_answer appends /chat/completions
                # (eval_aria_llm.py:331). Passing the full path doubles it
                # and every call 404s. Caught by the pilot.
                judge_url="https://api.deepseek.com/v1",
                judge_model="deepseek-chat", judge_api_key=judge_key,
                question=r.get("question") or "",
                expected=r.get("expected_answer") or "",
                actual=r.get("actual_answer") or "",
                context=r.get("_ctx") or "",
                expected_keywords=r.get("_kw") or [],
            )
        v = jr.get("verdict", "unscored")
        was = bool(r.get("passed"))
        # A judge that could NOT score must never silently demote a passing
        # answer. Treating 'unscored' as a failure is the same
        # absence-read-as-measurement defect this whole re-score exists to
        # correct — and the pilot produced 3 phantom flips that way before the
        # URL bug was fixed. Keep the stored verdict and count it separately.
        if not jr.get("ok", True) or v == "unscored":
            r["verdict_rescored"] = "unscored"
            r["passed_rescored"] = was          # unchanged, not failed
            r["judge_reason_rescored"] = "judge unavailable — stored verdict kept"
            changed["unscored"] = changed.get("unscored", 0) + 1
            return
        now = v == "correct"
        r["verdict_rescored"] = v
        r["passed_rescored"] = now
        r["judge_reason_rescored"] = (jr.get("reason") or "")[:200]
        changed[v] = changed.get(v, 0) + 1
        if now != was:
            flipped += 1

    done = 0
    for i in range(0, len(todo), 25):
        chunk = todo[i:i + 25]
        await asyncio.gather(*(one(r) for r in chunk))
        done += len(chunk)
        print(f"  {done}/{len(todo)} re-judged  (flipped so far: {flipped})")

    # Recompute the headline honestly.
    for r in results:
        r.pop("_ctx", None)
        r.pop("_kw", None)
    total = len(results)
    passed_new = sum(1 for r in results
                     if r.get("passed_rescored", r.get("passed")))
    stored = dd.get("passed", sum(1 for r in results if r.get("passed")))
    print(f"\n{'='*62}")
    print(f"  stored   : {stored}/{total} = {stored/total:.3f}")
    print(f"  rescored : {passed_new}/{total} = {passed_new/total:.3f}")
    print(f"  verdicts on the re-judged bucket: {changed}")
    print(f"  flipped  : {flipped} of {len(todo)}")
    g = buckets["grounded"]
    gp = sum(1 for r in g if r.get("passed"))
    if g:
        print(f"  GROUNDED-ONLY (unchanged by this fix): {gp}/{len(g)} = {gp/len(g):.3f}")
    _uns = changed.get("unscored", 0)
    if todo and _uns / len(todo) > 0.10:
        print(f"  !! {_uns}/{len(todo)} rows were UNSCORED — the judge failed on "
              f"more than 10%. Treat the rescored figure as NOT MEASURED.")
    print(f"{'='*62}")

    run.setdefault("_rescore", {}).update({
        "r_number": "R-F4333",
        "basis": "R-F4332 grounding tri-state; only 'unsupported' re-judged",
        "stored_passed": stored,
        "rescored_passed": passed_new,
        "total": total,
        "rejudged": len(todo),
        "flipped": flipped,
        "verdicts": changed,
        "buckets": {m: len(b) for m, b in buckets.items()},
    })
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(run, ensure_ascii=False, indent=1),
                        encoding="utf-8", newline="\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
