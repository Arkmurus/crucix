"""R-F3407 — capture CONTRADICTION traces: a no-match screen beside adverse coverage.

Both tool payloads are REAL: the screen comes from the live compliance endpoint,
the coverage from the live search. Nothing here invents a disagreement — the
capture SKIPS any subject where the tools happen to agree, because a manufactured
contradiction would be exactly the fabrication training this corpus exists to
avoid.

Subjects are drawn from the clean/listed side of the roster rather than the
sanctioned side: the whole point is an entity that screens clean, so a designated
entity has nothing to teach here. Several of the financial institutions carry
real, public enforcement histories, which is what makes the disagreement genuine
rather than contrived.

    python -m scripts.train.capture_contradiction \
        --out data/training/aria_tooluse_contradiction_v1.jsonl \
        --eval-blocklist data/training/_eval_blocklist_v1.txt
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from scripts.train._subjects import (
    FINANCIAL_INSTITUTIONS, INTERNATIONAL_PRIMES, LISTED_CLEAN,
    STATE_OWNED_ENTERPRISES,
)
from scripts.train.build_tooluse_corpus import (
    _norm_subject, build_contradiction_trace, write_multihop_corpus, validate_trace,
)


REQUIRED_ENV = ("ARIA_INTERNAL_TOKEN",)


def check_preconditions() -> None:
    # R-F3416 — imported INSIDE check_preconditions, not at module level.
    # This module is also the VALIDATOR, and the eval harness imports it on a pod
    # that only receives scripts/train/*. A module-level `import aria_service` for
    # a CLI-only concern made the whole file unimportable there, and the first real
    # cycle died at the baseline eval after paying for a pod, a GPU and a 60s model
    # load. The dependency is real but it belongs to this one function.
    from aria_service.env_bootstrap import load_project_env, require_env

    load_project_env()
    require_env(REQUIRED_ENV, purpose="capturing contradiction traces")


def _roster() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for group in (FINANCIAL_INSTITUTIONS, LISTED_CLEAN,
                  INTERNATIONAL_PRIMES, STATE_OWNED_ENTERPRISES):
        for s in group:
            if s not in seen:
                seen.add(s); out.append(s)
    return out


def select_capture_subjects(
    subjects: list[str], *, forbidden_subjects: set[str], limit: int = 0,
) -> list[str]:
    """Deduplicate and exclude protected subjects before any paid request."""
    if not forbidden_subjects:
        raise ValueError("forbidden subjects are empty; capture contamination is unchecked")
    selected: list[str] = []
    seen: set[str] = set()
    for subject in subjects:
        normalized = _norm_subject(subject)
        if not normalized or normalized in seen or normalized in forbidden_subjects:
            continue
        selected.append(subject.strip())
        seen.add(normalized)
        if limit and len(selected) == limit:
            break
    if not selected:
        raise ValueError("no novel capture subjects remain after exclusions")
    return selected


async def capture(subjects: list[str], base: str, token: str) -> list[dict]:
    import httpx
    traces: list[dict] = []
    agreed = 0
    async with httpx.AsyncClient(timeout=180.0) as c:   # no-breaker: offline corpus tool
        for s in subjects:
            try:
                sr = await c.post(f"{base}/api/aria/compliance/screen",
                                  headers={"Authorization": f"Bearer {token}"},
                                  json={"entity_name": s})
                if sr.status_code != 200:
                    print(f"  SKIP {s}: screen HTTP {sr.status_code}", file=sys.stderr)
                    continue
                screen = sr.json()

                wr = await c.post(f"{base}/api/aria/search/web",
                                  headers={"Authorization": f"Bearer {token}"},
                                  json={"query": f"{s} investigation OR fine OR allegations OR probe",
                                        "max_results": 6})
                if wr.status_code != 200:
                    print(f"  SKIP {s}: search HTTP {wr.status_code}", file=sys.stderr)
                    continue
                search = wr.json()
            except Exception as e:                      # noqa: BLE001
                print(f"  SKIP {s}: {type(e).__name__}: {e}", file=sys.stderr)
                continue

            t = build_contradiction_trace(s, screen, search)
            if t is None:
                agreed += 1
                print(f"  skip {s:<38} tools agree — no contradiction to teach", file=sys.stderr)
                continue
            errs = validate_trace(t)
            if errs:
                print(f"  SKIP {s}: {errs[0]}", file=sys.stderr)
                continue
            traces.append(t)
            print(f"  captured {s:<36} no-match screen + adverse coverage", file=sys.stderr)
    print(f"  ({agreed} subjects skipped: the tools agreed)", file=sys.stderr)
    return traces


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--eval-blocklist", type=Path)
    ap.add_argument("--subjects-file", type=Path,
                    help="One explicit novel subject per line instead of the static roster")
    ap.add_argument("--exclude-file", type=Path, action="append", default=[],
                    help="Repeat JSONL files whose subjects must not be queried")
    ap.add_argument("--allow-unchecked-contamination", action="store_true")
    ap.add_argument("--base", default=os.getenv("ARIA_SERVICE_URL", "https://aria-intel.fly.dev"))
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    check_preconditions()

    token = os.getenv("ARIA_INTERNAL_TOKEN", "")
    blocklist = None
    if a.eval_blocklist:
        blocklist = [ln.strip() for ln in a.eval_blocklist.read_text(encoding="utf-8").splitlines()
                     if ln.strip() and not ln.startswith("#")]

    if a.subjects_file:
        source_subjects = [
            line.strip() for line in a.subjects_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    else:
        source_subjects = _roster()
    forbidden = {_norm_subject(subject) for subject in (blocklist or [])} - {""}
    for path in a.exclude_file:
        forbidden |= {
            _norm_subject(str(json.loads(line).get("subject") or ""))
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        } - {""}
    subs = select_capture_subjects(
        source_subjects, forbidden_subjects=forbidden, limit=a.limit,
    )
    traces = asyncio.run(capture(subs, a.base.rstrip("/"), token))
    n = write_multihop_corpus(traces, a.out, eval_subjects=blocklist,
                              allow_unchecked=a.allow_unchecked_contamination)
    print(f"wrote {n} validated contradiction traces -> {a.out} (from {len(traces)} real pairs)")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
