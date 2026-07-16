"""R-F2677 — connected-components independent-origin count.

An independent origin is a distinct editorial voice that isn't echoing another. Two sources
are the SAME origin if they share a PUBLISHER (one voice, N facts) OR a STORY (syndication /
verbatim PR). This fixes the R-F2671 live-eval residual where 3 turdef.com articles counted
as 3 independent sources. The existing golden gate (FP-rate 0, recall 1.0) must still hold.
"""

from __future__ import annotations

from aria_service.intel.dd_independent_verifier import (
    count_independent_origins,
    is_independently_corroborated,
)
from aria_service.intel.dd_independence_eval import run_v2_eval


# ── the new behaviour: same publisher, different stories → ONE origin ─────────

def test_same_publisher_multiple_stories_is_one_origin() -> None:
    """The turdef.com ×3 case from the live eval: one publisher, 3 distinct stories."""
    srcs = [
        {"domain": "turdef.com", "story": "serial_production"},
        {"domain": "turdef.com", "story": "roketsan_buys"},
        {"domain": "turdef.com", "story": "dynaflow_acq"},
    ]
    assert count_independent_origins(srcs) == 1
    assert is_independently_corroborated(srcs) is False


def test_two_publishers_one_with_multiple_stories() -> None:
    """turdef ×3 (→1) + one genuine third party (→1) = 2 origins."""
    srcs = [
        {"domain": "turdef.com", "story": "a"},
        {"domain": "turdef.com", "story": "b"},
        {"domain": "turdef.com", "story": "c"},
        {"domain": "theguardian.com", "story": "guardian_probe"},
    ]
    assert count_independent_origins(srcs) == 2
    assert is_independently_corroborated(srcs) is True


# ── preserved behaviour ──────────────────────────────────────────────────────

def test_wire_syndication_still_one_origin() -> None:
    srcs = [
        {"domain": "reuters.com", "story": "wire42"},
        {"domain": "uk.reuters.com", "story": "wire42"},
        {"domain": "somepaper.com", "story": "wire42"},
    ]
    assert count_independent_origins(srcs) == 1


def test_genuine_multi_publisher_distinct_stories() -> None:
    srcs = [
        {"domain": "bbc.co.uk", "story": "a"},
        {"domain": "theguardian.com", "story": "b"},
        {"domain": "ft.com", "story": "c"},
        {"domain": "nytimes.com", "story": "d"},
    ]
    assert count_independent_origins(srcs) == 4


def test_authorities_and_internal() -> None:
    assert count_independent_origins(["companies_house", "sec_edgar"]) == 2
    assert count_independent_origins(["companies_house", "aria_knowledge"]) == 1  # internal excluded
    assert count_independent_origins(["ghost_scorer", "network_walker"]) == 0


def test_dict_wrapped_internal_still_excluded() -> None:
    """Pass-2 defence-in-depth: an internal provenance label wrapped in a dict must NOT be
    read as an independent publisher (would otherwise falsely corroborate)."""
    assert count_independent_origins([{"source": "aria_knowledge"}, {"source": "ghost_scorer"}]) == 0
    # a real external domain in a dict is still a publisher (not misread as internal)
    assert count_independent_origins([{"domain": "ghostblog.com", "story": "a"}]) == 1


def test_transitive_merge_is_conservative() -> None:
    """A publisher bridging two stories (reuters covers X and syndicated-Y, bbc syndicates Y)
    merges to ONE origin — the SAFE direction (under-count → 'not corroborated') for a DD
    tool. Documented as intentional, not a bug."""
    srcs = [
        {"domain": "reuters.com", "story": "X"},
        {"domain": "reuters.com", "story": "Y"},
        {"domain": "bbc.co.uk", "story": "Y"},
    ]
    assert count_independent_origins(srcs) == 1


# ── the offline GATE must still pass ─────────────────────────────────────────

def test_golden_gate_unchanged_fp0_recall1() -> None:
    res = run_v2_eval()
    assert res["false_positive_rate"] == 0.0, res.get("false_positive_cases")
    assert res["recall"] == 1.0, res.get("false_negative_cases")
    assert res["precision"] == 1.0
