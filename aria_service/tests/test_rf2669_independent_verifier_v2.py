"""R-F2669 — C-3 v2 independent-verification classifier + its golden-set eval.

The classifier resolves each source to an INDEPENDENT ORIGIN (content-story fingerprint
when known, else publisher family, internal excluded) and corroborates a claim only at
>=2 distinct origins. The eval GATE for flipping R-F2413 is unchanged: false_positive
rate MUST be 0. C-3 v2's win over v1: recall rises to 1.0 (genuine multi-publisher now
corroborated) while wire syndication stays correctly rejected (one story = one origin).
"""

from __future__ import annotations

from aria_service.intel.dd_independent_verifier import (
    count_independent_origins,
    is_independently_corroborated,
    origin_key,
    publisher_family,
    registrable_domain,
)
from aria_service.intel.dd_independence_eval import run_v1_eval, run_v2_eval


# ── the origin model ─────────────────────────────────────────────────────────

def test_registrable_domain_strips_subdomains_and_www() -> None:
    assert registrable_domain("uk.reuters.com") == "reuters.com"
    assert registrable_domain("https://www.bbc.co.uk/news/x") == "bbc.co.uk"
    assert registrable_domain("theguardian.com") == "theguardian.com"
    assert registrable_domain("m.somepaper.com/a/b?c=1") == "somepaper.com"


def test_publisher_family_collapses_known_families() -> None:
    assert publisher_family("bbc.com") == publisher_family("bbc.co.uk") == "pub:bbc"
    assert publisher_family("uk.reuters.com") == publisher_family("reuters.com") == "pub:reuters"
    assert publisher_family("nytimes.com") == "pub:nytimes.com"  # unknown → its own domain


def test_content_story_dedups_wire_syndication() -> None:
    """The case domain-family cannot catch: one wire story on 3 different sites."""
    syndicated = [
        {"domain": "reuters.com", "story": "W1"},
        {"domain": "uk.reuters.com", "story": "W1"},
        {"domain": "somepaper.com", "story": "W1"},
    ]
    assert count_independent_origins(syndicated) == 1
    assert is_independently_corroborated(syndicated) is False


def test_distinct_stories_are_distinct_origins() -> None:
    distinct = [
        {"domain": "bbc.co.uk", "story": "A"},
        {"domain": "theguardian.com", "story": "B"},
        {"domain": "ft.com", "story": "C"},
    ]
    assert count_independent_origins(distinct) == 3
    assert is_independently_corroborated(distinct) is True


def test_internal_and_authorities() -> None:
    assert origin_key("ghost_scorer") == "internal"
    assert origin_key("network_walker") == "internal"
    assert origin_key("aria_knowledge") == "internal"
    assert origin_key("sanctions:ofac") == "sanctions:ofac"
    assert origin_key("companies_house") == "companies_house"
    assert count_independent_origins(["companies_house", "aria_knowledge"]) == 1  # internal excluded


def test_external_domains_not_misread_as_internal_compute() -> None:
    """Pass-1 fix: a real publisher domain that happens to start with 'ghost'/'network'
    must resolve to its publisher family, NOT ARIA's internal 'ghost_scorer' compute."""
    assert origin_key("ghostblog.com") == "pub:ghostblog.com"
    assert origin_key("network-news.com") == "pub:network-news.com"
    # two such external domains DO corroborate (they are independent publishers)
    assert is_independently_corroborated(["ghostblog.com", "network-news.com"]) is True


# ── the eval GATE ────────────────────────────────────────────────────────────

def test_v2_false_positive_rate_is_zero_THE_R_F2413_GATE() -> None:
    res = run_v2_eval()
    assert res["false_positive_rate"] == 0.0, (
        "R-F2413 GATE VIOLATED — false positives (claims wrongly 'independently "
        f"verified'): {res['false_positive_cases']}"
    )
    assert res["precision"] == 1.0


def test_v2_recall_reaches_the_target_1_0() -> None:
    """C-3 v2 must corroborate genuine multi-publisher (v1's false negative) — recall 1.0."""
    res = run_v2_eval()
    assert res["recall"] == 1.0, f"v2 must close the recall gap; false negatives: {res['false_negative_cases']}"
    assert res["fn"] == 0


def test_v2_strictly_improves_on_v1() -> None:
    """v1: FP=0, recall 0.8 (undercounts multi-publisher). v2: FP=0, recall 1.0.
    The FP gate holds for both; v2 raises recall — the exact acceptance criterion."""
    v1 = run_v1_eval()
    v2 = run_v2_eval()
    assert v1["false_positive_rate"] == 0.0 and v2["false_positive_rate"] == 0.0
    assert v2["recall"] > v1["recall"]
    assert "genuine_multi_publisher" in v1["false_negative_cases"]
    assert "genuine_multi_publisher" not in v2["false_negative_cases"]


def test_v2_still_rejects_wire_syndication_the_hard_case() -> None:
    """The whole point: v2 does NOT false-green a syndicated wire story."""
    v2 = run_v2_eval()
    assert "wire_syndication" not in v2["false_positive_cases"]
