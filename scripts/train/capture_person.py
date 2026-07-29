"""R-F3409 — capture PERSON screening traces: a name match is not an identification.

Screening a person is a different task shape from screening a company, and the
corpus only ever taught the second. The failure mode here is the mirror of the
false clean: an innocent individual flagged because a listed name resembles
theirs.

Both designated persons AND common names are captured. The common names matter
as much: they produce the no-match case, where the honest answer is "not found
by NAME" rather than "this person is clear" — a no-match on a person is weaker
evidence than it looks, because transliteration varies and no identifiers are
returned to match against.

    python -m scripts.train.capture_person \
        --out data/training/aria_tooluse_person_v1.jsonl \
        --eval-blocklist data/training/_eval_blocklist_v1.txt
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from scripts.train._subjects import DESIGNATED_PERSONS
from scripts.train.build_tooluse_corpus import (
    build_person_screen_trace, write_multihop_corpus, validate_trace,
)


REQUIRED_ENV = ("ARIA_INTERNAL_TOKEN",)

# Ordinary names, deliberately included. These generate the no-match half of the
# axis and are exactly the people a name-only screen endangers.
COMMON_NAMES: list[str] = [
    "John Smith", "Maria Garcia", "Wei Zhang", "Mohammed Ali",
    "Anna Kowalski", "David Jones", "Sarah Ahmed", "Carlos Silva",
    "Yuki Tanaka", "Priya Patel", "Ivan Petrov", "Fatima Hassan",
]


def check_preconditions() -> None:
    # R-F3416 — imported INSIDE check_preconditions, not at module level.
    # This module is also the VALIDATOR, and the eval harness imports it on a pod
    # that only receives scripts/train/*. A module-level `import aria_service` for
    # a CLI-only concern made the whole file unimportable there, and the first real
    # cycle died at the baseline eval after paying for a pod, a GPU and a 60s model
    # load. The dependency is real but it belongs to this one function.
    from aria_service.env_bootstrap import load_project_env, require_env

    load_project_env()
    require_env(REQUIRED_ENV, purpose="capturing person screening traces")


async def capture(subjects: list[str], base: str, token: str) -> list[dict]:
    import httpx
    traces: list[dict] = []
    async with httpx.AsyncClient(timeout=180.0) as c:   # no-breaker: offline corpus tool
        for s in subjects:
            try:
                r = await c.post(f"{base}/api/aria/compliance/screen",
                                 headers={"Authorization": f"Bearer {token}"},
                                 json={"entity_name": s})
                if r.status_code != 200:
                    print(f"  SKIP {s}: HTTP {r.status_code}", file=sys.stderr)
                    continue
                payload = r.json()
            except Exception as e:                      # noqa: BLE001
                print(f"  SKIP {s}: {type(e).__name__}: {e}", file=sys.stderr)
                continue
            t = build_person_screen_trace(s, payload)
            if t is None:
                print(f"  SKIP {s}: no trace built", file=sys.stderr)
                continue
            errs = validate_trace(t)
            if errs:
                print(f"  SKIP {s}: {errs[0]}", file=sys.stderr)
                continue
            n = len((payload.get("sanctions") or {}).get("matches") or [])
            traces.append(t)
            print(f"  captured {s:<34} {'name match x%d' % n if n else 'no match'}",
                  file=sys.stderr)
    return traces


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--eval-blocklist", type=Path)
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

    subs = DESIGNATED_PERSONS + COMMON_NAMES
    subs = subs[: a.limit] if a.limit else subs
    traces = asyncio.run(capture(subs, a.base.rstrip("/"), token))
    n = write_multihop_corpus(traces, a.out, eval_subjects=blocklist,
                              allow_unchecked=a.allow_unchecked_contamination)
    print(f"wrote {n} validated person traces -> {a.out} (from {len(traces)} real screens)")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
