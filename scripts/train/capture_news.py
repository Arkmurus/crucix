"""R-F3374 — capture news→exposure traces from REAL retrieval.

The live search genuinely mixes ARIA's own `memory://` records in with web
results, so these traces teach the distinction that matters: an outside outlet
corroborates, ARIA's own memory does not. A claim supported only by what she
already believed is single-source, and saying so is the correct answer.

    python -m scripts.train.capture_news --out data/training/aria_tooluse_news_v1.jsonl \
        --eval-blocklist data/training/_eval_blocklist_v1.txt
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from scripts.train._subjects import NEWS_SUBJECTS
from scripts.train.build_tooluse_corpus import (
    build_news_impact_trace, write_multihop_corpus, validate_trace, _independent_sources,
)


async def capture(subjects: list[str], base: str, token: str) -> list[dict]:
    import httpx
    traces: list[dict] = []
    async with httpx.AsyncClient(timeout=180.0) as c:   # no-breaker: offline corpus tool
        for s in subjects:
            try:
                r = await c.post(f"{base}/api/aria/search/web",
                                 headers={"Authorization": f"Bearer {token}"},
                                 json={"query": f"{s} news", "max_results": 6})
                if r.status_code != 200:
                    print(f"  SKIP {s}: HTTP {r.status_code}", file=sys.stderr)
                    continue
                payload = r.json()
            except Exception as e:                      # noqa: BLE001
                print(f"  SKIP {s}: {type(e).__name__}: {e}", file=sys.stderr)
                continue
            t = build_news_impact_trace(s, payload)
            errs = validate_trace(t)
            if errs:
                print(f"  SKIP {s}: {errs[0]}", file=sys.stderr)
                continue
            n_ind = len(_independent_sources(payload))
            traces.append(t)
            print(f"  captured {s:<36} independent_sources={n_ind}", file=sys.stderr)
    return traces


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--eval-blocklist", type=Path)
    ap.add_argument("--allow-unchecked-contamination", action="store_true")
    ap.add_argument("--base", default=os.getenv("ARIA_SERVICE_URL", "https://aria-intel.fly.dev"))
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    token = os.getenv("ARIA_INTERNAL_TOKEN", "")
    if not token:
        print("ARIA_INTERNAL_TOKEN not set", file=sys.stderr)
        return 2
    blocklist = None
    if a.eval_blocklist:
        blocklist = [ln.strip() for ln in a.eval_blocklist.read_text(encoding="utf-8").splitlines()
                     if ln.strip() and not ln.startswith("#")]

    subs = NEWS_SUBJECTS[: a.limit] if a.limit else NEWS_SUBJECTS
    traces = asyncio.run(capture(subs, a.base.rstrip("/"), token))
    n = write_multihop_corpus(traces, a.out, eval_subjects=blocklist,
                              allow_unchecked=a.allow_unchecked_contamination)
    print(f"wrote {n} validated news traces -> {a.out} (from {len(traces)} real searches)")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
