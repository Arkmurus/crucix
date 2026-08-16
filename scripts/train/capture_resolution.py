"""R-F3372 — capture ENTITY-RESOLUTION traces from real registry ambiguity.

Short names are what operators actually type, and the register's ranking is
dangerous for them: "Chemring" ranks the DISSOLVED Chemring Limited first and the
live Chemring Group plc fourth; "Babcock" ranks a dissolved company first;
"QinetiQ" puts an unrelated PAWSTOPURR LTD second.

Each trace teaches the model to resolve the subject BEFORE any downstream hop,
and to ask rather than guess when the register does not answer confidently. The
ambiguity is real registry data — none of it is authored.

    python -m scripts.train.capture_resolution --out data/training/aria_tooluse_resolution_v1.jsonl \
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
    _norm_subject, build_resolution_trace, write_multihop_corpus, validate_trace,
    resolve_company,
)

from scripts.train._subjects import AMBIGUOUS_SHORT, UK_REGISTRY_SUBJECTS


# R-F3398 — refuse to run credential-less. Without these the tooling cannot
# tell "nothing found" from "never looked", and it wrote the second as the
# first for 44 subjects before this existed.
REQUIRED_ENV = ("COMPANIES_HOUSE_API_KEY",)


def check_preconditions() -> None:
    # R-F3416 — imported INSIDE check_preconditions, not at module level.
    # This module is also the VALIDATOR, and the eval harness imports it on a pod
    # that only receives scripts/train/*. A module-level `import aria_service` for
    # a CLI-only concern made the whole file unimportable there, and the first real
    # cycle died at the baseline eval after paying for a pod, a GPU and a 60s model
    # load. The dependency is real but it belongs to this one function.
    from aria_service.env_bootstrap import load_project_env, require_env

    load_project_env()
    require_env(REQUIRED_ENV, purpose="resolving ambiguous short company names against the register")


# Short names are where the register misleads; the full names are the control.
# R-F3396 — the slice was a fixed [:8], so widening the roster could never
# reach this axis. Take the full registry roster: disambiguation is exactly
# the skill that needs breadth, not a sample of it.
SUBJECTS = AMBIGUOUS_SHORT + UK_REGISTRY_SUBJECTS


def select_capture_subjects(
    subjects: list[str], *, forbidden_subjects: set[str], limit: int = 0,
) -> list[str]:
    """Deduplicate and exclude protected subjects before registry requests."""
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


async def capture(subjects: list[str]) -> list[dict]:
    from aria_service.intel import companies_house as ch
    traces: list[dict] = []
    for s in subjects:
        try:
            results = await ch.search_companies(s, limit=5)
            if not results:
                print(f"  SKIP {s}: no registry results", file=sys.stderr)
                continue
            payload = {"results": results[:5]}
            trace = build_resolution_trace(s, payload)
            errs = validate_trace(trace)
            if errs:
                print(f"  SKIP {s}: {errs[0]}", file=sys.stderr)
                continue
            chosen, reason, ambiguous = resolve_company(s, results[:5])
            verdict = ("ASK" if (chosen is None or ambiguous)
                       else f"-> {chosen.get('company_number')}")
            traces.append(trace)
            print(f"  captured {s:<34} {verdict}", file=sys.stderr)
        except Exception as e:                              # noqa: BLE001
            print(f"  SKIP {s}: {type(e).__name__}: {e}", file=sys.stderr)
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
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    check_preconditions()

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
        source_subjects = SUBJECTS
    forbidden = {_norm_subject(subject) for subject in (blocklist or [])} - {""}
    for path in a.exclude_file:
        forbidden |= {
            _norm_subject(str(json.loads(line).get("subject") or ""))
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        } - {""}
    subjects = select_capture_subjects(
        source_subjects, forbidden_subjects=forbidden, limit=a.limit,
    )
    traces = asyncio.run(capture(subjects))
    n = write_multihop_corpus(traces, a.out, eval_subjects=blocklist,
                              allow_unchecked=a.allow_unchecked_contamination)
    print(f"wrote {n} validated resolution traces -> {a.out} (from {len(traces)} built)")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
