"""R-F3369 — capture challenge-the-user traces from REAL screens.

For every subject BOTH premises are built, which covers all four quadrants with
no synthetic data at all:

    real HIT   + user says "clean"      -> ARIA refuses to confirm (capitulation case)
    real HIT   + user says "sanctioned" -> ARIA agrees (guard must not go blind)
    real CLEAN + user says "sanctioned" -> ARIA refuses the accusation (defamation case)
    real CLEAN + user says "clean"      -> ARIA agrees

The premise is authored around genuine tool output, never the other way round:
nothing here decides what the screen "should" say.

    python -m scripts.train.capture_challenge --out data/training/aria_tooluse_challenge_v1.jsonl \
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
    build_challenge_trace, write_multihop_corpus, validate_trace, _matches, _was_performed,
)

from scripts.train._subjects import SANCTIONED, LISTED_CLEAN


# R-F3398 — refuse to run credential-less. Without these the tooling cannot
# tell "nothing found" from "never looked", and it wrote the second as the
# first for 44 subjects before this existed.
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
    require_env(REQUIRED_ENV, purpose="capturing challenge-the-premise traces")


# R-F3374 — the shared roster. Both halves matter: designated entities give the
# real HITs, listed companies the real CLEANs, and each subject is built in BOTH
# premise directions so all four quadrants come from real evidence.
SUBJECTS = SANCTIONED + LISTED_CLEAN


async def capture(subjects: list[str], base: str, token: str) -> list[dict]:
    import httpx
    traces: list[dict] = []
    async with httpx.AsyncClient(timeout=120.0) as c:   # no-breaker: offline corpus tool
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

            stance = ("not screened" if not _was_performed(payload)
                      else ("HIT" if _matches(payload) else "clean"))
            for premise in ("clean", "sanctioned"):
                t = build_challenge_trace(s, payload, premise=premise)
                errs = validate_trace(t)
                if errs:
                    print(f"  SKIP {s}/{premise}: {errs[0]}", file=sys.stderr)
                    continue
                traces.append(t)
            print(f"  captured {s} (evidence={stance})", file=sys.stderr)
    return traces


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--eval-blocklist", type=Path)
    ap.add_argument("--allow-unchecked-contamination", action="store_true")
    ap.add_argument("--base", default=os.getenv("ARIA_SERVICE_URL", "https://aria-intel.fly.dev"))
    a = ap.parse_args()
    check_preconditions()

    token = os.getenv("ARIA_INTERNAL_TOKEN", "")
    if not token:
        print("ARIA_INTERNAL_TOKEN not set", file=sys.stderr)
        return 2
    blocklist = None
    if a.eval_blocklist:
        blocklist = [ln.strip() for ln in a.eval_blocklist.read_text(encoding="utf-8").splitlines()
                     if ln.strip() and not ln.startswith("#")]

    traces = asyncio.run(capture(SUBJECTS, a.base.rstrip("/"), token))
    n = write_multihop_corpus(traces, a.out, eval_subjects=blocklist,
                              allow_unchecked=a.allow_unchecked_contamination)
    print(f"wrote {n} validated challenge traces -> {a.out} (from {len(traces)} built)")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
