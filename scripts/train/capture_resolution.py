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
from collections import Counter
import json
import os
import sys
from pathlib import Path

from scripts.train.build_tooluse_corpus import (
    _norm_for_derivation, _norm_subject, build_resolution_trace,
    write_multihop_corpus, validate_trace, resolve_company,
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


RESOLUTION_CASES = (
    "confident_exact",
    "confident_core",
    "multiple_live",
    "dissolved_only",
    "unresolved",
)


def classify_resolution_case(subject: str, results: list[dict]) -> str:
    """Classify the decision branch established by a real registry response."""
    chosen, reason, ambiguous = resolve_company(subject, results)
    if ambiguous:
        return "multiple_live"
    if chosen is not None:
        title = str(chosen.get("title") or "")
        return (
            "confident_exact"
            if _norm_for_derivation(title) == _norm_for_derivation(subject)
            else "confident_core"
        )
    if "only name match" in reason and "dissolved" in reason:
        return "dissolved_only"
    return "unresolved"


def enforce_resolution_coverage(
    traces: list[dict], *, required_cases: dict[str, int],
) -> dict[str, int]:
    """Reject a capture whose measured registry branches are under-covered."""
    unknown = sorted(set(required_cases) - set(RESOLUTION_CASES))
    if unknown:
        raise ValueError(f"unknown resolution cases: {', '.join(unknown)}")
    counts: Counter[str] = Counter()
    for trace in traces:
        messages = trace.get("messages") or []
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        if not tool_messages:
            raise ValueError(f"resolution trace has no registry payload: {trace.get('subject')!r}")
        try:
            payload = json.loads(tool_messages[-1].get("content") or "{}")
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"resolution trace has invalid registry payload: {trace.get('subject')!r}"
            ) from exc
        results = payload.get("results") or []
        counts[classify_resolution_case(str(trace.get("subject") or ""), results)] += 1
    missing = {
        case: minimum - counts[case]
        for case, minimum in required_cases.items()
        if counts[case] < minimum
    }
    if missing:
        detail = ", ".join(
            f"{case}={counts[case]}/{required_cases[case]}" for case in sorted(missing)
        )
        raise ValueError(f"resolution branch coverage is insufficient: {detail}")
    return dict(counts)


def parse_required_case(values: list[str]) -> dict[str, int]:
    """Parse repeatable CASE=MINIMUM coverage requirements."""
    required: dict[str, int] = {}
    for value in values:
        case, separator, raw_minimum = value.partition("=")
        if not separator or case not in RESOLUTION_CASES:
            raise ValueError(f"invalid --require-case {value!r}")
        try:
            minimum = int(raw_minimum)
        except ValueError as exc:
            raise ValueError(f"invalid --require-case {value!r}") from exc
        if minimum < 1 or case in required:
            raise ValueError(f"invalid --require-case {value!r}")
        required[case] = minimum
    return required


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
    ap.add_argument(
        "--require-case", action="append", default=[], metavar="CASE=MINIMUM",
        help="Require measured registry branch coverage before writing any rows",
    )
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
    required_cases = parse_required_case(a.require_case)
    if required_cases:
        counts = enforce_resolution_coverage(traces, required_cases=required_cases)
        print(f"measured resolution cases: {json.dumps(counts, sort_keys=True)}")
    n = write_multihop_corpus(traces, a.out, eval_subjects=blocklist,
                              allow_unchecked=a.allow_unchecked_contamination)
    print(f"wrote {n} validated resolution traces -> {a.out} (from {len(traces)} built)")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
