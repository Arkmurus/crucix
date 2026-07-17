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


_GOLDEN_V2 = (
    pathlib.Path(__file__).resolve().parents[2]
    / "data" / "eval" / "dd_independence_golden_v2.json"
)


def v2_verifier_classifier(sources: list) -> bool:
    """C-3 v2 (R-F2669): the real independent-origin verifier — content-story dedup +
    publisher-family. In the offline eval the golden set supplies each source's `story`
    fingerprint (what the LIVE DD re-fetch computes); the classifier logic is identical."""
    from .dd_independent_verifier import is_independently_corroborated
    return is_independently_corroborated(list(sources))


def run_v2_eval() -> dict[str, Any]:
    """Score the C-3 v2 verifier against the v2 golden set (with content fingerprints)."""
    g = load_golden(_GOLDEN_V2)
    return score_independence(g.get("cases") or [], v2_verifier_classifier)


# =============================================================================
# R-F2688 — BROADEN the eval so the R-F2677 residual is MEASURED, not asserted.
#
# The v2 golden set contains no PR-echo case, so it structurally CANNOT score a
# PR-echo detector: it would report a clean sheet for a classifier that fabricates
# independence on the exact input the residual describes. An eval that cannot fail
# is the same species of vacuous gate as a key nothing writes (cf. gates #3/#4/#6).
#
# v3 adds:
#   - PR-echo cases: one company PR reworded by N outlets → N distinct stories →
#     the shipped publisher-or-story classifier calls them N independent origins.
#     Labelled False. These are EXPECTED to score as FALSE POSITIVES today — that
#     is the residual becoming visible, NOT a regression to fix by relabelling.
#   - Discriminators: two genuinely independent newsrooms on the SAME event (very
#     high topical similarity, no shared origin) → labelled True. These are what
#     stop the fix from being "lower a similarity threshold until the FP vanishes":
#     cosine measures TOPIC, not ORIGIN, so a naive merge destroys real recall.
#
# The GATE remains false_positive_rate == 0 (R-F2413): a claim wrongly marked
# "independently corroborated" is the honesty-USP betrayal. Report recall honestly
# alongside it — a recall drop is a real cost to weigh, never a number to hide.
# =============================================================================

_GOLDEN_V3 = (
    pathlib.Path(__file__).resolve().parents[2]
    / "data" / "eval" / "dd_independence_golden_v3.json"
)


def run_v3_eval(classifier: "Callable[[list], bool] | None" = None) -> dict[str, Any]:
    """Score a corroboration classifier against the BROADENED v3 golden set.

    Defaults to the currently-shipped v2 verifier, which makes the residual visible:
    the PR-echo cases are expected to land in `false_positive_cases` until a
    PR-echo-aware classifier (C-3 v3) is passed in. Pass `classifier` to score a
    candidate without touching this module.
    """
    g = load_golden(_GOLDEN_V3)
    return score_independence(g.get("cases") or [], classifier or v2_verifier_classifier)


# =============================================================================
# R-F2690 — WIRE the C-3 v3 text-level detector into the eval.
#
# Integration gap R-F2687 flagged, and it is real: `v2_verifier_classifier` calls
# `is_independently_corroborated(sources)`, which reads each source's PRECOMPUTED
# `story` LABEL. The PR-echo detector works at TEXT level, inside the clustering
# path (`cluster_stories_with_echo`). So scoring R-F2687 through the v2 classifier
# would exercise NONE of it and report the residual as still-open — a false
# negative ABOUT THE FIX, which is as dishonest as a false pass.
#
# This classifier reproduces what the LIVE DD path does: re-fetch → cluster the
# TEXT (lexical near-dup + PR-echo merge) → count origins on the COMPUTED story
# ids. Sources without text (every v2 case) keep their golden `story` label, so v2
# cases score exactly as before — v3 stays a strict superset regression.
# =============================================================================

def _computed_story_sources(sources: list) -> list:
    """Replace each source's golden `story` with the story id CLUSTERED FROM ITS TEXT.

    Sources lacking `text` are passed through untouched (their golden label stands).
    """
    import asyncio

    from .dd_independent_verifier import cluster_stories_with_echo

    texted = {
        s["domain"]: s["text"]
        for s in sources
        if isinstance(s, dict) and s.get("text") and s.get("domain")
    }
    if not texted:
        return list(sources)

    stories, _echo_detail = asyncio.run(cluster_stories_with_echo(texted))

    out = []
    for s in sources:
        if not isinstance(s, dict) or not s.get("text"):
            out.append(s)
            continue
        sid = stories.get(s["domain"])
        c = dict(s)
        # No story id == content too thin to fingerprint. Keep it story-less rather
        # than falling back to the golden label: inventing a distinct label here
        # would manufacture an origin, the exact fabrication under test.
        c["story"] = sid if sid else None
        out.append(c)
    return out


def v3_echo_classifier(sources: list) -> bool:
    """C-3 v3: cluster the TEXT (lexical + PR-echo), then count independent origins.

    This is the shape the live DD path runs, so the score is attributable to the
    detector rather than to the fixture's precomputed labels.
    """
    from .dd_independent_verifier import is_independently_corroborated

    return is_independently_corroborated(_computed_story_sources(sources))


def run_v3_echo_eval() -> dict[str, Any]:
    """Score the C-3 v3 text-level detector (R-F2687) against the v3 golden set."""
    return run_v3_eval(v3_echo_classifier)


def residual_report(classifier: "Callable[[list], bool] | None" = None) -> dict[str, Any]:
    """v3 score split into the PR-echo residual vs the rest, for an at-a-glance verdict.

    `gate_met` is the honest bottom line: the R-F2413 flag may only ever flip while
    false_positive_rate == 0 across the WHOLE set — the residual cases included.
    """
    g = load_golden(_GOLDEN_V3)
    cases = g.get("cases") or []
    clf = classifier or v2_verifier_classifier
    overall = score_independence(cases, clf)
    residual_ids = {
        c.get("id") for c in cases
        if str(c.get("id") or "").startswith("pr_echo_")
    }
    return {
        "overall": overall,
        "gate_met": overall["false_positive_rate"] == 0.0,
        "residual_case_ids": sorted(residual_ids),
        "residual_failures": sorted(
            residual_ids.intersection(overall["false_positive_cases"])
        ),
        "discriminator_failures": sorted(
            set(overall["false_negative_cases"]) - residual_ids
        ),
    }
