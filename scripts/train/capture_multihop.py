"""R-F3367 — capture REAL 3-hop DD chains: registry -> officers -> sanctions screen.

Every payload is a genuine execution. Nothing here invents a company number, an
officer or a screening result — the derivation guard in build_tooluse_corpus
would reject the trace anyway, which is the point of having it.

    python -m scripts.train.capture_multihop --out data/training/aria_tooluse_multihop_v1.jsonl \
        --eval-blocklist data/training/_eval_blocklist_v1.txt
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from scripts.train.build_tooluse_corpus import (
    build_multihop_trace, write_multihop_corpus, validate_trace, resolve_company,
)

# Listed public companies — public record, and deliberately NOT customer DDs.
SUBJECTS = [
    "Rolls-Royce Holdings plc", "Unilever plc", "Tesco plc",
    "Babcock International Group plc", "QinetiQ Group plc", "Serco Group plc",
    "Chemring Group plc", "Meggitt plc", "Ultra Electronics Holdings plc",
    "Melrose Industries plc", "Smiths Group plc", "Cobham Limited",
]


async def _screen_live(name: str, base: str, token: str) -> dict | None:
    import httpx
    async with httpx.AsyncClient(timeout=120.0) as c:   # no-breaker: offline corpus tool
        r = await c.post(f"{base}/api/aria/compliance/screen",
                         headers={"Authorization": f"Bearer {token}"},
                         json={"entity_name": name})
        return r.json() if r.status_code == 200 else None


async def capture(subjects: list[str], base: str, token: str) -> list[dict]:
    from aria_service.intel import companies_house as ch
    traces: list[dict] = []
    for subject in subjects:
        try:
            search = await ch.search_companies(subject, limit=3)
            if not search:
                print(f"  SKIP {subject}: no registry match", file=sys.stderr)
                continue
            # R-F3372 — do NOT trust the registry's ranking. Against the real
            # register "Chemring" ranks the DISSOLVED Chemring Limited first and
            # the live Chemring Group plc fourth; "Babcock" ranks a dissolved
            # company first. Capturing results[0] would mint traces that run due
            # diligence on a shell, and every later hop would inherit the error.
            top, reason, ambiguous = resolve_company(subject, search)
            if top is None or ambiguous:
                print(f"  SKIP {subject}: unresolved ({reason})", file=sys.stderr)
                continue
            number = str(top.get("company_number") or "").strip()
            if not number:
                print(f"  SKIP {subject}: no company_number", file=sys.stderr)
                continue

            officers = await ch.get_officers(number)
            officers = [o for o in (officers or []) if not o.get("resigned_on")]
            if not officers:
                print(f"  SKIP {subject}: no serving officers", file=sys.stderr)
                continue
            officer_name = str(officers[0].get("name") or "").strip()

            screen = await _screen_live(officer_name, base, token)
            if screen is None:
                print(f"  SKIP {subject}: screen failed for {officer_name}", file=sys.stderr)
                continue

            trace = build_multihop_trace(subject, [
                ("companies_house_search", {"query": subject}, {"results": search[:3]}),
                ("companies_house_officers", {"company_number": number},
                 {"company_number": number, "officers": officers[:10]}),
                ("screen", {"entity_name": officer_name}, screen),
            ])
            errs = validate_trace(trace)
            if errs:
                print(f"  SKIP {subject}: {errs[0]}", file=sys.stderr)
                continue
            traces.append(trace)
            print(f"  captured {subject} -> {number} -> {officer_name}", file=sys.stderr)
        except Exception as e:                              # noqa: BLE001
            print(f"  SKIP {subject}: {type(e).__name__}: {e}", file=sys.stderr)
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

    subs = SUBJECTS[: a.limit] if a.limit else SUBJECTS
    traces = asyncio.run(capture(subs, a.base.rstrip("/"), token))
    n = write_multihop_corpus(traces, a.out, eval_subjects=blocklist,
                              allow_unchecked=a.allow_unchecked_contamination)
    print(f"wrote {n} validated multi-hop traces -> {a.out} (from {len(traces)} real chains)")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
