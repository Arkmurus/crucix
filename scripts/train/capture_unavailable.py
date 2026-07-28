"""R-F3375 — capture REAL source-unavailable screens: the never-false-clean case.

THE GAP THIS CLOSES. Every corpus shipped so far (R-F3366/3367/3369/3372/3374)
had ZERO not-screened traces, because the live service kept screening
successfully. The never-false-clean behaviour was covered by the validator and by
unit fixtures, but was NOT represented in real data — and a fixture is exactly
what this repo has been burned by before ("fixtures LIED: 7/7 green, 0/20 real").

NOTHING HERE IS SYNTHESISED. The payloads come from `sanctions.screen_with_aliases`
executed for real against a genuinely unreachable source, returning its real
documented contract: `screened=False, error='sanctions_source_unavailable',
source_unavailable=True, matches=[]`. The unavailability is real, not simulated —
the module could not reach OpenSanctions and said so. The only reshaping is
wrapping that genuine result in the same `{"sanctions": ...}` envelope the
compliance endpoint returns, so the traces are comparable with the others.

WHY THESE SUBJECTS. They are entities that ARE sanctioned when the source IS
reachable — verified in the R-F3369 capture, where Sberbank returned a real OFAC
match. So the lesson is the highest-stakes one available: the screen failed on an
entity that genuinely is designated, which means treating "no matches" as a clean
result would have cleared a sanctioned counterparty. That is the exact failure
never-false-clean exists to prevent, and it can only be taught honestly with a
real failure on a real hit.

    python -m scripts.train.capture_unavailable --out data/training/aria_tooluse_unavailable_v1.jsonl \
        --eval-blocklist data/training/_eval_blocklist_v1.txt
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from scripts.train.build_tooluse_corpus import (
    build_trace, build_challenge_trace, write_multihop_corpus, validate_trace, _was_performed,
)

# Entities known to be genuinely designated (proven by real hits in the
# R-F3369 capture). A failed screen on a KNOWN hit is the sharpest lesson.
SUBJECTS = [
    "Sberbank", "Gazprombank", "Bank Rossiya", "VTB Bank", "Alfa-Bank",
    "Kalashnikov Concern", "Almaz-Antey", "Rosneft", "Rostec", "Sovcomflot",
    "Bank Melli Iran", "Mahan Air", "Belaruskali", "Aeroflot",
]


async def capture(subjects: list[str]) -> list[dict]:
    from aria_service.intel import sanctions
    traces: list[dict] = []
    for s in subjects:
        try:
            result = await sanctions.screen_with_aliases(s, source="registry")
        except Exception as e:                                  # noqa: BLE001
            print(f"  SKIP {s}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        # Wrap the GENUINE module result in the endpoint's envelope so these
        # traces are shaped like the rest. The sanctions block is untouched.
        payload = {
            "status": "ERROR", "result": "UNKNOWN", "blocked": False,
            "entity": s, "sanctions": result,
        }
        if _was_performed(payload):
            print(f"  SKIP {s}: the source WAS reachable — not an unavailable case",
                  file=sys.stderr)
            continue
        for t in (build_trace(s, payload),
                  build_challenge_trace(s, payload, premise="clean")):
            errs = validate_trace(t)
            if errs:
                print(f"  SKIP {s}/{t['label']}: {errs[0]}", file=sys.stderr)
                continue
            traces.append(t)
        print(f"  captured {s:<26} source_unavailable (real)", file=sys.stderr)
    return traces


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--eval-blocklist", type=Path)
    ap.add_argument("--allow-unchecked-contamination", action="store_true")
    a = ap.parse_args()

    blocklist = None
    if a.eval_blocklist:
        blocklist = [ln.strip() for ln in a.eval_blocklist.read_text(encoding="utf-8").splitlines()
                     if ln.strip() and not ln.startswith("#")]

    traces = asyncio.run(capture(SUBJECTS))
    if not traces:
        print("no unavailable screens captured — the source was reachable throughout. "
              "This corpus can only be built while a source genuinely cannot be reached.",
              file=sys.stderr)
        return 1
    n = write_multihop_corpus(traces, a.out, eval_subjects=blocklist,
                              allow_unchecked=a.allow_unchecked_contamination)
    print(f"wrote {n} validated not-screened traces -> {a.out} (from {len(traces)} built)")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
