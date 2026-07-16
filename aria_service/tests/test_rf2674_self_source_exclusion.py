"""R-F2674 — self-source exclusion in independent verification.

The R-F2671 LIVE eval found the model counting the SUBJECT'S OWN websites as independent
origins (Modirum: 4 of 5 counted origins were modirum*.com; Assan: its own site +
assanpanel.com). A company's own site is never an independent witness to itself. These
tests lock the fix against the exact real-world patterns the eval surfaced.
"""

from __future__ import annotations

import pytest

from aria_service.intel.dd_independent_verifier import (
    assess_independent_verification,
    is_self_source,
    subject_domain_tokens,
)


# ── token derivation ─────────────────────────────────────────────────────────

def test_subject_tokens_drop_generic_words() -> None:
    assert subject_domain_tokens("Modirum Gespi") == frozenset({"modirum", "gespi"})
    assert subject_domain_tokens("Assan Group") == frozenset({"assan"})  # 'group' dropped
    # KNOWN LIMITATION: a <4-char acronym ("BAE") is below min_len → no token → its own
    # site won't be recognised as a self-source. Conservative (undercount exclusions, never
    # over-exclude a third party); acceptable — documented, not silent.
    assert subject_domain_tokens("BAE Systems plc") == frozenset()
    # generic/industry words never survive as distinctive tokens
    assert subject_domain_tokens("Global Defence Systems Group") == frozenset()
    assert subject_domain_tokens("") == frozenset()


# ── self-source recognition (the real domains from the live eval) ─────────────

def test_self_source_matches_subject_own_domains() -> None:
    modirum = subject_domain_tokens("Modirum Gespi")
    assert is_self_source("https://modirumplatforms.com/news/x", modirum) is True
    assert is_self_source("https://modirumgespi.com/en/news/y", modirum) is True
    assert is_self_source("https://modirumdefence.com/portfolio", modirum) is True
    assan = subject_domain_tokens("Assan Group")
    assert is_self_source("https://assangroup.com.tr/", assan) is True
    assert is_self_source("https://assanpanel.com/en", assan) is True


def test_self_source_does_NOT_match_third_parties() -> None:
    modirum = subject_domain_tokens("Modirum Gespi")
    for u in ("https://www.reuters.com/x", "https://defence-industry.eu/y",
              "https://www.theguardian.com/z", "https://caliber.az/en/post/w"):
        assert is_self_source(u, modirum) is False, u
    # unknown subject (no tokens) never excludes anything
    assert is_self_source("https://modirumgespi.com/x", frozenset()) is False


def test_pass2_no_false_exclusion_of_real_publishers() -> None:
    """Pass-2 CONFIRMED FP class — the dangerous direction for a DD tool. A subject whose
    distinctive token is a publisher-substring must NOT exclude genuine outlets:
      - prefix match (not substring) rejects SUFFIX collisions (washingtonpost/foxnews);
      - publisher-root stopwords drop the token entirely for media-named subjects.
    """
    # "Post Holdings" → 'post' is a publisher-root stopword → dropped → no exclusions at all
    assert subject_domain_tokens("Post Holdings Inc") == frozenset()
    post = subject_domain_tokens("Post Holdings Inc")
    for u in ("https://www.washingtonpost.com/a", "https://nypost.com/b", "https://huffpost.com/c"):
        assert is_self_source(u, post) is False, u
    # "News Corp" / "Times Group" likewise → their tokens are dropped
    assert subject_domain_tokens("News Corp") == frozenset()
    assert subject_domain_tokens("Times Group") == frozenset()
    news = subject_domain_tokens("News Corp")
    for u in ("https://www.foxnews.com/a", "https://www.nbcnews.com/b"):
        assert is_self_source(u, news) is False, u
    # A NON-media subject whose token is only a SUFFIX of an outlet is safe via prefix match:
    # subject 'Acme' must not exclude 'someacme.com' (suffix), only 'acmecorp.com' (prefix).
    acme = subject_domain_tokens("Acme Robotics")
    assert is_self_source("https://someacme.com/x", acme) is False
    assert is_self_source("https://acmerobotics.com/x", acme) is True


# ── the report-level assessment (mocked re-fetch) ────────────────────────────

_STORY_A = ("the company announced today a major acquisition of a latin american defence "
            "firm in a deal that strengthens its regional footprint across several markets "
            "and adds substantial manufacturing capacity according to the official statement")
_STORY_B = ("regulators approved a new framework for export controls affecting a broad range "
            "of dual use goods with exporters now required to file additional declarations "
            "and undergo enhanced screening before any licence can be granted by officials")
_STORY_C = ("an independent trade publication reported separately on the firm's expansion "
            "into new markets citing multiple sources familiar with the strategy and noting "
            "the competitive landscape has shifted considerably over the past several years")


@pytest.mark.asyncio
async def test_modirum_pattern_self_sources_excluded(monkeypatch) -> None:
    """Modirum-like: 4 of the subject's OWN domains + 1 genuine third party. After
    self-source exclusion → 1 independent origin, NOT independently corroborated."""
    report = {"target": {"name": "Modirum Gespi"}, "digital": {"press_coverage": [
        {"url": "https://modirumplatforms.com/news/ceo"},
        {"url": "https://modirumplatforms.com/news/ceo-appointed"},
        {"url": "https://modirumgespi.com/en/news/new-style"},
        {"url": "https://modirumgespi.com/en/news/acquisition"},
        {"url": "https://defence-industry.eu/acquisition-latam"},
    ]}}
    bodies = {
        "https://modirumplatforms.com/news/ceo": _STORY_A,
        "https://modirumplatforms.com/news/ceo-appointed": _STORY_B,
        "https://modirumgespi.com/en/news/new-style": _STORY_C,
        "https://modirumgespi.com/en/news/acquisition": _STORY_A,
        "https://defence-industry.eu/acquisition-latam": _STORY_C,
    }

    async def _fetch(url):
        return ("ok", bodies[url])

    res = await assess_independent_verification(report, fetcher=_fetch)
    assert res["refetched_ok"] == 5
    assert res["self_sources_excluded"] == 4, res["per_url"]
    assert res["independent_press_origins"] == 1  # only defence-industry.eu
    assert res["press_independently_corroborated"] is False


@pytest.mark.asyncio
async def test_assan_pattern_own_site_excluded_third_parties_kept(monkeypatch) -> None:
    """Assan-like: own site (3 near-dup pages) + own panel site are excluded; genuine
    third-party outlets remain and DO corroborate."""
    report = {"entity": "Assan Group", "digital": {"press_coverage": [
        {"url": "https://assangroup.com.tr/"},
        {"url": "https://assangroup.com.tr/corporate/about-us/"},
        {"url": "https://assanpanel.com/en"},
        {"url": "https://turdef.com/article/serial-production"},
        {"url": "https://www.defenceturkey.com/en/content/saha-expo"},
        {"url": "https://www.theguardian.com/law/unaoil"},
    ]}}
    bodies = {
        "https://assangroup.com.tr/": _STORY_A,
        "https://assangroup.com.tr/corporate/about-us/": _STORY_A,
        "https://assanpanel.com/en": _STORY_B,
        "https://turdef.com/article/serial-production": _STORY_A,
        "https://www.defenceturkey.com/en/content/saha-expo": _STORY_B,
        "https://www.theguardian.com/law/unaoil": _STORY_C,
    }

    async def _fetch(url):
        return ("ok", bodies[url])

    res = await assess_independent_verification(report, fetcher=_fetch)
    assert res["self_sources_excluded"] == 3  # 2 assangroup pages (1 story) + assanpanel
    # 3 distinct third-party stories (turdef, defenceturkey, theguardian) remain
    assert res["independent_press_origins"] == 3
    assert res["press_independently_corroborated"] is True


@pytest.mark.asyncio
async def test_unknown_subject_excludes_nothing(monkeypatch) -> None:
    """Conservative: with no resolvable subject name, nothing is excluded (no over-exclusion)."""
    report = {"digital": {"press_coverage": [
        {"url": "https://foo.com/a"}, {"url": "https://bar.com/b"},
    ]}}
    bodies = {"https://foo.com/a": _STORY_A, "https://bar.com/b": _STORY_B}

    async def _fetch(url):
        return ("ok", bodies[url])

    res = await assess_independent_verification(report, fetcher=_fetch)
    assert res["self_sources_excluded"] == 0
    assert res["independent_press_origins"] == 2
