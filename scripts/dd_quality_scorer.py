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


async def score_dd_quality(llm: Any, label: str = "ci-dd-quality") -> dict[str, Any]:
    """Run the golden DD eval set and return quality scores.

    Args:
        llm: An LLMProvider instance (or None to skip LLM-dependent tests).
        label: Label for this eval run.

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

    # Save new baseline
    _save_baseline(score, label)

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
    args = parser.parse_args()

    result = await score_dd_quality(llm=None, label=args.label)

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
