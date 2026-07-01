"""R-F2248 — credibility tier match is SUFFIX (registrable-domain), not substring,
so a hostile domain can't inherit a trusted tier; + new official domains seeded."""
from __future__ import annotations
from aria_service.intel.web_search import _score_credibility


def test_legit_tier1_and_subdomain():
    assert _score_credibility("https://www.un.org/sc/x") == 1
    assert _score_credibility("https://scsanctions.un.org/list") == 1  # genuine subdomain


def test_substring_attack_rejected():
    # domains that merely CONTAIN a trusted one must NOT get its tier
    assert _score_credibility("https://notun.org/x") != 1
    assert _score_credibility("https://un.org.evil.com/x") != 1
    assert _score_credibility("https://gov.uk.phishing.ru/x") != 1


def test_new_official_domains_tiered():
    assert _score_credibility("https://www.defense.gov/News/Contracts/") == 1
    assert _score_credibility("https://reliefweb.int/updates") == 2


def test_disinfo_still_quarantined():
    assert _score_credibility("https://rt.com/x") == 6
