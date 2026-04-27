"""Regression guards for the F32+F39+F33+F28 mixed batch.

F32 — SSRN paywall: live evidence 2026-04-27 20:14:43 — papers.ssrn.com
returns 403, archive.is fallback returns 429. Adding ssrn hosts to the
known-paywall denylist short-circuits the wasted fetch chain.

F39 — sanctions validator was passing 4-6 word sentence fragments with a
single stopword (e.g. "Iran nexus before engagement", "Iran is openly
signalling it will"). Live evidence: 12 garbage queries still slipped
through to OpenSanctions /search after the F2 commit. Tightened: any
input with ≥1 stopword AND >4 words is now rejected.

F33 — heatmap surfaced "latam" as a weak-cell slug; the display-name
mapping had no entry, so the fallback Title-cased to "Latam". Google
News returned 0 results on every "Latam X Y Z" query. Map now has
"latam": "Latin America" (the natural English term that search engines
actually index).

F28 — covered by lifespan setting NODE_OPTIONS=--no-deprecation; not
test-exercised here because verifying environment-variable propagation
to a Lightpanda Node subprocess requires fly.io infra.
"""
from __future__ import annotations

from aria_service.intel.researcher import _is_known_paywall
from aria_service.intel.sanctions import _looks_like_entity_name
from aria_service.learning.research_engine import _region_display_name


# ── F32 — SSRN now on paywall list ────────────────────────────────────────

def test_ssrn_papers_blocked():
    """The exact 2026-04-27 evidence URL must be skipped."""
    assert not _is_known_paywall("https://www.reuters.com/world/article")  # sanity
    assert _is_known_paywall("https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6623719")
    assert _is_known_paywall("https://www.ssrn.com/abstract=6623719")
    assert _is_known_paywall("https://ssrn.com/abstract=6623719")


# ── F39 — sentence fragments with one stopword now rejected ───────────────

def test_iran_nexus_before_engagement_rejected():
    """Live failure 2026-04-27 20:36:09 — F2 validator passed this."""
    assert not _looks_like_entity_name("Iran nexus before engagement")


def test_iran_is_openly_signalling_it_will_rejected():
    """Live failure 2026-04-27 20:36:10 — passed previous validator."""
    assert not _looks_like_entity_name("Iran is openly signalling it will")


def test_4word_with_stopword_rejected():
    """Borderline cases that are clearly fragments."""
    for query in [
        "Iran of foreign policy",
        "China and the West",
        "Russia is at war",
        "Africa as a market",
    ]:
        assert not _looks_like_entity_name(query), f"should reject: {query!r}"


# ── F39 — legitimate entity names still pass ──────────────────────────────

def test_short_entity_with_one_stopword_passes():
    """Real names with a single stopword and ≤4 words must still pass:
    the Bank-of-America shape is the design constraint."""
    for name in [
        "Bank of America",      # 3 words, 1 stopword (of)
        "Boeing of Seattle",    # 3 words, 1 stopword (of)
        "Ministry of Defence",  # 3 words, 1 stopword (of)
        "The Boeing Company",   # 3 words, 1 stopword (the)
    ]:
        assert _looks_like_entity_name(name), f"should pass: {name!r}"


def test_real_entities_still_pass():
    """Defensive: regression on the curated entity list from F2 tests."""
    for name in [
        "Northrop Grumman",
        "BAE Systems",
        "Lockheed Martin Corporation",
        "Hanwha Aerospace",
        "Rosoboronexport",
        "ALADA",
        "GAMI",
        "SIMPORTEX",
    ]:
        assert _looks_like_entity_name(name), f"should pass: {name!r}"


# ── F33 — latam region slug now resolves to Latin America ────────────────

def test_latam_slug_resolves_to_latin_america():
    """Live failure 2026-04-27 20:20:25 — Google News returned 0 on
    every 'Latam X Y Z' query because the heatmap surfaced 'latam' as
    a weak-cell slug and the display-name mapping fell through to
    Title-case ('Latam') instead of 'Latin America'."""
    assert _region_display_name("latam") == "Latin America"


def test_latam_non_lusophone_still_works():
    """The pre-existing slug must still resolve correctly."""
    assert _region_display_name("latam_non_lusophone") == "Latin America"


def test_spain_latam_resolves():
    """corpus_registry tags some sources as spain_latam — needs a name too."""
    assert _region_display_name("spain_latam") == "Spain Latin America"


def test_unknown_slug_still_falls_back_to_title_case():
    """Unknown slugs still get the Title-case fallback so the system
    doesn't break on a new region added to the heatmap."""
    assert _region_display_name("brand_new_region") == "Brand New Region"
