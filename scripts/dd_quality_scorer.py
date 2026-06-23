"""
R-F1835 — DD quality scorer.
Runs the golden eval set and reports score + delta vs baseline.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("aria.dd_scorer")

# Baseline score file — stores the last known good score
_BASELINE_FILE = Path(__file__).resolve().parent.parent / "data" / "dd_quality_baseline.json"
# Threshold for alerting on score drop
_SCORE_DROP_THRESHOLD = 0.05  # 5 percentage points


async def score_dd_quality(llm: Any, label: str = "ci-dd-quality",
                           update_baseline: bool = False) -> dict[str, Any]:
    """Run the golden DD eval set and return quality scores.

    Args:
        llm: An LLMProvider instance (or None to skip LLM-dependent tests).
        label: Label for this eval run.
        update_baseline: R-F1840 — whether this run is allowed to LOWER the
            committed baseline. Default False: a regression must NOT silently
            move the bar (see below). The baseline is still SEEDED on the first
            run (no baseline yet) and RATCHETED UP automatically on improvement.

    Returns:
        Dict with keys: score, previous_score, delta, total, passed, failed.
    """
    from aria_service.intel.eval_runner import run_eval

    result = await run_eval(llm, label=label)
    if not result or not isinstance(result, dict):
        return {"error": "eval_runner returned no result", "score": 0.0, "total": 0}

    entries = result.get("entries") or result.get("results") or []
    total = len(entries)
    if total == 0:
        return {"error": "no golden entries found", "score": 0.0, "total": 0}

    passed = sum(1 for e in entries if e.get("passed") or e.get("score", 0) >= 0.5)
    score = round(passed / total, 4)

    # Load previous baseline
    previous_score = _load_baseline()
    delta = round(score - previous_score, 4) if previous_score is not None else None

    # R-F1840 — baseline-ratchet fix. The old code ALWAYS overwrote the baseline
    # with the current score, so a regressed run moved the bar down to its own
    # level and the SAME regression never tripped again (--fail-on-regression
    # was a no-op across runs, and the baseline silently ratcheted toward 0).
    # Now: seed if absent, ratchet UP on improvement, and only LOWER the
    # baseline when explicitly told to (deliberate re-baselining), never on a
    # CI/regression-check run.
    if previous_score is None:
        _save_baseline(score, label)          # seed the first baseline
    elif score > previous_score:
        _save_baseline(score, label)          # ratchet up — new high-water mark
    elif update_baseline:
        _save_baseline(score, label)          # deliberate re-baseline (operator)
    # else: regression/equal → leave the committed baseline untouched so the
    # drop stays detectable on every subsequent run.

    return {
        "score": score,
        "previous_score": previous_score,
        "delta": delta,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "label": label,
        "threshold": _SCORE_DROP_THRESHOLD,
        "regression": delta is not None and delta < -_SCORE_DROP_THRESHOLD,
    }


def _load_baseline() -> float | None:
    """Load the previous DD quality score from disk."""
    try:
        if _BASELINE_FILE.exists():
            data = json.loads(_BASELINE_FILE.read_text())
            return data.get("score")
    except Exception:
        pass
    return None


def _save_baseline(score: float, label: str) -> None:
    """Save the current DD quality score to disk."""
    try:
        _BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _BASELINE_FILE.write_text(json.dumps({
            "score": score,
            "label": label,
            "timestamp": __import__("datetime").datetime.now().isoformat(),
        }))
    except Exception as e:
        logger.warning("Failed to save DD quality baseline: %s", e)


async def main():
    """CLI entry point — run DD quality scorer and print results."""
    import argparse

    parser = argparse.ArgumentParser(description="DD Quality Scorer")
    parser.add_argument("--label", default="ci-dd-quality", help="Label for this run")
    parser.add_argument("--fail-on-regression", action="store_true",
                        help="Exit with code 1 if score dropped below threshold")
    parser.add_argument("--update-baseline", action="store_true",
                        help="Deliberately re-baseline to this run's score (the only "
                             "way to LOWER the committed baseline; R-F1840)")
    args = parser.parse_args()

    result = await score_dd_quality(llm=None, label=args.label,
                                    update_baseline=args.update_baseline)

    print(f"DD Quality Score: {result.get('score', 'N/A')}")
    print(f"Previous: {result.get('previous_score', 'N/A')}")
    print(f"Delta: {result.get('delta', 'N/A')}")
    print(f"Total: {result.get('total', 0)}")
    print(f"Passed: {result.get('passed', 0)}")
    print(f"Failed: {result.get('failed', 0)}")

    if result.get("error"):
        print(f"Error: {result['error']}")
        sys.exit(1)

    if args.fail_on_regression and result.get("regression"):
        print(f"REGRESSION: Score dropped by {abs(result['delta']):.1%} "
              f"(threshold: {_SCORE_DROP_THRESHOLD:.1%})")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
