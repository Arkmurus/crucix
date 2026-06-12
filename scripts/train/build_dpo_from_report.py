"""build_dpo_from_report — DPO preference pairs OFFLINE from an eval report (R-F1521).

Unlike build_dpo_from_eval.py (R-F1336), which re-serves the model to generate
the "rejected" samples, this reads them straight from a judge eval report whose
result rows already store BOTH the model's answer and the golden answer
(eval_aria_llm.py emits actual_answer/expected_answer/verdict per row, R-F1469+).
So no GPU/serving is needed — the DPO dataset is built for free from the run we
already paid for.

Recipe (same as R-F1336): for every golden question the model got WRONG, pair
the golden expected answer (chosen) against the model's own answer (rejected).
This trains the next model to prefer the calibrated/honest answer over exactly
the failure modes it exhibited.

Quality filters (CLAUDE.md §24 — training must be REAL):
  - only rows the judge marked wrong/partial (a real failure to correct)
  - chosen = expected_answer, must be non-empty and substantive (>= 20 chars)
  - rejected = actual_answer, must be non-empty (the failure to train away from)
  - drop chosen == rejected (no preference signal)
  - dedupe by prompt
  - EXCLUDED: nothing from R-F59/R-F80 adversarial runs (this report is the
    golden DD set only — no inverted-label risk from the adversarial grater bug).

Usage:
  python scripts/train/build_dpo_from_report.py \
    --report data/eval_reports/aria_llm_v0_4_eval.json \
    --out data/training/aria_dpo_v04.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

MIN_CHOSEN_LEN = 20   # a golden answer shorter than this is not a useful target
MIN_REJECTED_LEN = 1  # an empty rejected still teaches "don't go blank", but keep it explicit


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", type=Path, required=True, help="eval_aria_llm.py JSON report")
    ap.add_argument("--out", type=Path, required=True, help="output DPO JSONL")
    ap.add_argument("--max-pairs", type=int, default=600)
    args = ap.parse_args()

    rep = json.loads(args.report.read_text(encoding="utf-8"))
    dd = rep.get("defence_dd") or rep.get("dd_eval") or {}
    rows = dd.get("results") or []
    if not rows:
        print(f"FATAL: no defence_dd.results in {args.report}")
        return 1

    pairs = []
    seen = set()
    skipped = {"passed": 0, "empty_chosen": 0, "empty_rejected": 0, "identical": 0, "dup": 0}
    for r in rows:
        verdict = (r.get("verdict") or "").lower()
        passed = r.get("passed")
        # a failure = judge said wrong/partial, or the boolean pass flag is False
        is_fail = (verdict in ("wrong", "partial")) or (passed is False)
        if not is_fail:
            skipped["passed"] += 1
            continue
        prompt = (r.get("question") or "").strip()
        chosen = (r.get("expected_answer") or "").strip()
        rejected = (r.get("actual_answer") or "").strip()
        if len(chosen) < MIN_CHOSEN_LEN:
            skipped["empty_chosen"] += 1
            continue
        if len(rejected) < MIN_REJECTED_LEN:
            skipped["empty_rejected"] += 1
            continue
        if chosen == rejected:
            skipped["identical"] += 1
            continue
        if prompt in seen:
            skipped["dup"] += 1
            continue
        seen.add(prompt)
        pairs.append({
            "prompt": prompt,
            "chosen": chosen,
            "rejected": rejected,
            "meta": {"topic": r.get("topic"), "verdict": verdict, "source": "v0.4_eval_report"},
        })
        if len(pairs) >= args.max_pairs:
            break

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    print(f"DPO pairs written: {len(pairs)} -> {args.out}")
    print(f"skipped: {skipped}")
    # topic spread (where the failures cluster — the DPO signal)
    from collections import Counter
    topics = Counter(p["meta"]["topic"] for p in pairs)
    print("top failure topics:", topics.most_common(8))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
