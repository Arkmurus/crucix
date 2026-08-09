"""R-F2699 — separate a PR echo from real reporting by PROVENANCE, not similarity.

The last C-3 false positive: two outlets paraphrase one press release, quote nothing
verbatim. Measured, neither existing signal can catch it:

  - COSINE cannot separate. The echo scores 0.572 while genuine corroborations score
    0.603 and 0.673 — the echo is LESS similar than the real ones. Lowering the 0.90
    gate sweeps up real corroboration first. (R-F2687 measured the same shape:
    echo .572 / witness .741 / echo .903 — the witness sits BETWEEN the echoes.)
  - PR-MARKER-ON-BOTH is not sufficient (R-F2698). Real investigations SEEK COMMENT,
    so a genuine corroboration carries the marker too: independent_investigations_
    seeking_comment is expected=True with pr_marker_both=True at sim=0.504 — LOWER
    than the echo. No threshold orders them.

What actually differs is whether anyone DID ANY REPORTING. The echo relays only the
announcement. Both independent cases carry their own sourcing: "has spoken to eleven
former suppliers", "documents seen by this newspaper", "analysis of filings by".

So: a PR-marked pair is an echo ONLY IF NEITHER side shows independent sourcing. That
asks about provenance, not topic — which is why it separates where cosine cannot.

DIRECTION OF ERROR (matters for a DD tool): a missed own-reporting phrase merges two
real witnesses -> FN -> "not corroborated", the conservative/safe error. A stray
own-reporting phrase in an echo -> no merge -> the FP we already have. So the signal
cannot make the dangerous direction worse than today.
"""
from __future__ import annotations

import asyncio

import pytest

# R-F3805 — this one case reaches the semantic-similarity branch, and the
# TF-IDF fallback used when sentence-transformers is absent scores this pair
# 0.179 against a 0.45 threshold. With the real embedder it is ~0.572 (the
# figure detect_pr_echo's own docstring records), so the verdict flips on the
# EMBEDDER, not on the code under test. ENVIRONMENT gap (§16: no win-arm64
# wheel); it runs in the Linux image.
from ._env_probe import requires_module

from aria_service.intel.dd_independent_verifier import (
    detect_pr_echo,
    has_own_reporting,
    has_pr_marker,
)

# Two trade outlets, each paraphrasing the same company announcement. No verbatim
# quote, PR marker on both, and neither did any reporting of its own.
_ECHO_A = (
    "The group has opened its new facility, according to a press release. The company "
    "said the site would create local jobs and expand production capacity."
)
_ECHO_B = (
    "A new plant has been opened by the manufacturer, the firm announced today. It said "
    "in a statement that the investment supports its regional growth plans."
)

# Two real newsrooms. Both sought comment (PR marker on both) but each has its OWN
# sourcing — this is the case a marker-only rule would destroy.
_REAL_A = (
    "The BBC has spoken to eleven former suppliers who say invoices went unpaid for "
    "more than a year. Two provided correspondence supporting their account. Approached "
    "for comment, the company said in a statement that it rejects the characterisation."
)
_REAL_B = (
    "Analysis of filings by the Financial Times shows payment terms lengthening well "
    "beyond the sector norm, a finding corroborated by three suppliers interviewed by "
    "this newspaper. In a statement, the firm said it rejects the characterisation."
)


def test_own_reporting_detects_a_newsrooms_own_sourcing():
    assert has_own_reporting(_REAL_A) is True
    assert has_own_reporting(_REAL_B) is True


def test_own_reporting_is_absent_from_a_pure_announcement_relay():
    assert has_own_reporting(_ECHO_A) is False
    assert has_own_reporting(_ECHO_B) is False


def test_the_fixtures_are_the_hard_case_not_a_strawman():
    """Both pairs carry a PR marker on BOTH sides — the signal must do the work."""
    for t in (_ECHO_A, _ECHO_B, _REAL_A, _REAL_B):
        assert has_pr_marker(t) is True


@requires_module("sentence_transformers")
def test_pr_marked_pair_with_no_own_reporting_is_an_echo():
    """CAPABILITY: the last C-3 false positive, closed."""
    is_echo, reason = asyncio.run(detect_pr_echo(_ECHO_A, _ECHO_B))
    assert is_echo is True
    assert "own_reporting" in reason or "pr_marker" in reason


def test_pr_marked_pair_WITH_own_reporting_is_not_an_echo():
    """THE GUARD (R-F2698): two real witnesses that both sought comment must survive."""
    is_echo, _ = asyncio.run(detect_pr_echo(_REAL_A, _REAL_B))
    assert is_echo is False


def test_one_sided_own_reporting_still_blocks_the_merge():
    """Conservative: if EITHER side did its own reporting, this is not a pure echo.

    Blocking the merge keeps claimed independence (the FP direction we already have),
    which is the wrong-but-not-worse error; merging on one-sided evidence could destroy
    a real witness, which is worse to get wrong silently.
    """
    is_echo, _ = asyncio.run(detect_pr_echo(_ECHO_A, _REAL_B))
    assert is_echo is False


def test_no_pr_marker_means_the_secondary_path_never_fires():
    """Two independent investigations that never mention a statement are untouched."""
    a = "The BBC has learned the regulator opened a formal investigation."
    b = "A formal inquiry is under way, the Financial Times understands."
    assert not (has_pr_marker(a) and has_pr_marker(b))
    is_echo, _ = asyncio.run(detect_pr_echo(a, b))
    assert is_echo is False


def test_shared_verbatim_quote_still_takes_precedence():
    """R-F2687/R-F2692's primary signal must be unaffected."""
    q = ' "This deal is a decisive step in our long-term strategy," said chief executive Marta Oliveira. '
    a = "The group completed the purchase, according to a press release." + q
    b = "The firm announced today that the deal closed." + q
    is_echo, reason = asyncio.run(detect_pr_echo(a, b))
    assert is_echo is True
    assert reason.startswith("shared_quote")
