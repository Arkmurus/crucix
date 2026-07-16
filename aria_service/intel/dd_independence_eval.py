"""R-F2666 — scoring harness for DD independent-source corroboration (C-3).

The FALSE-POSITIVE rate is the GATE for ever flipping
`independent_source_verification_run` (R-F2413): a claim wrongly marked
"independently corroborated" is the exact honesty-USP betrayal, so it MUST be 0.
False negatives (conservative undercount) are acceptable for C-3 v1; C-3 v2 plugs its
re-fetch/domain-family verifier into `score_independence(...)` as `classifier` and must
hold false_positive_rate == 0 while RAISING recall (closing v1's undercount of genuine
multi-publisher press). The operator reviews the eval result BEFORE the flag is flipped.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Callable

_GOLDEN = (
    pathlib.Path(__file__).resolve().parents[2]
    / "data" / "eval" / "dd_independence_golden_v1.json"
)


def load_golden(path: "str | pathlib.Path | None" = None) -> dict:
    return json.loads(pathlib.Path(path or _GOLDEN).read_text(encoding="utf-8"))


def score_independence(
    cases: list[dict], classifier: Callable[[list], bool]
) -> dict[str, Any]:
    """Score a corroboration `classifier(sources) -> bool` against labelled cases.

    Returns a confusion summary + the GATE metric false_positive_rate = FP / (FP+TN)
    (fraction of genuinely-not-corroborated claims the classifier wrongly flags).
    """
    tp = tn = fp = fn = 0
    fp_cases: list = []
    fn_cases: list = []
    for c in cases:
        pred = bool(classifier(c.get("sources") or []))
        exp = bool(c.get("expected"))
        if pred and exp:
            tp += 1
        elif (not pred) and (not exp):
            tn += 1
        elif pred and (not exp):
            fp += 1
            fp_cases.append(c.get("id"))
        else:
            fn += 1
            fn_cases.append(c.get("id"))
    negatives = fp + tn
    return {
        "n": len(cases), "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "false_positive_rate": round(fp / negatives, 4) if negatives else 0.0,
        "precision": round(tp / (tp + fp), 4) if (tp + fp) else 1.0,
        "recall": round(tp / (tp + fn), 4) if (tp + fn) else 1.0,
        "false_positive_cases": fp_cases,
        "false_negative_cases": fn_cases,
    }


def v1_corroboration_classifier(sources: list) -> bool:
    """The SHIPPED C-3 v1 label-based conservative classifier (R-F2662).

    A single claim backed by `sources` is 'independently corroborated' iff it has
    >=2 distinct non-internal origins under the conservative label grouping.
    """
    from .dd_orchestrator import _independent_corroboration
    count, _rate = _independent_corroboration([{"claim": "x", "sources": list(sources)}])
    return count >= 1


def run_v1_eval() -> dict[str, Any]:
    """Convenience: score the shipped v1 classifier against the golden set."""
    g = load_golden()
    return score_independence(g.get("cases") or [], v1_corroboration_classifier)
