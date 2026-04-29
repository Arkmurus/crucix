"""Regression test for F91 — paywall denylist catches DOI-host redirects.

Live evidence 2026-04-29 15:04:32:
  GET https://doi.org/10.2139/ssrn.6340699 → 302 Found
  GET https://www.ssrn.com/abstract=6340699 → 302 Found
  GET https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6340699 → 403 Forbidden
  Article ... returned 403 — trying archive
  archive.is/newest/... → 429
  wayback/available?url=... → 503 Service Unavailable

The original `_is_known_paywall` checks hostname only, so URLs coming
in as `doi.org/10.2139/ssrn.NNNN` slip through (host=doi.org) and
burn the full fetch chain even though papers.ssrn.com is on the
paywall list.

After the fix, DOI-host URLs whose path begins with a known-paywall
registrant prefix (10.2139 SSRN, 10.1016 Elsevier, 10.1093 OUP, etc.)
are recognised and skipped before the first GET.
"""
from __future__ import annotations


def test_doi_org_ssrn_prefix_recognised():
    from aria_service.intel.researcher import _is_known_paywall

    # The exact URL from the live log
    assert _is_known_paywall("https://doi.org/10.2139/ssrn.6340699")
    # Variants
    assert _is_known_paywall("https://doi.org/10.2139/ssrn.123")
    assert _is_known_paywall("http://dx.doi.org/10.2139/ssrn.999")


def test_doi_org_elsevier_prefix_recognised():
    """10.1016 = Elsevier (sciencedirect.com)."""
    from aria_service.intel.researcher import _is_known_paywall
    assert _is_known_paywall("https://doi.org/10.1016/j.cie.2026.111919")


def test_doi_org_oup_prefix_recognised():
    """10.1093 = OUP (academic.oup.com)."""
    from aria_service.intel.researcher import _is_known_paywall
    assert _is_known_paywall("https://doi.org/10.1093/jcsl/krx005")


def test_doi_org_wiley_prefix_recognised():
    """10.1002 = Wiley."""
    from aria_service.intel.researcher import _is_known_paywall
    assert _is_known_paywall("https://doi.org/10.1002/jae.2526")


def test_doi_org_other_publishers_not_recognised():
    """DOI prefixes NOT in the registrant denylist must still pass —
    e.g. eLife (10.7554), open journals, government DOIs. We only
    skip the known-paywalled ones."""
    from aria_service.intel.researcher import _is_known_paywall
    assert not _is_known_paywall("https://doi.org/10.7554/elife.92311.3.sa0")
    assert not _is_known_paywall("https://doi.org/10.21236/ad1009177")  # DTIC
    assert not _is_known_paywall("https://doi.org/10.5281/zenodo.123456")


def test_direct_paywall_hostnames_still_caught():
    """The original hostname check must still work — F32's coverage."""
    from aria_service.intel.researcher import _is_known_paywall
    assert _is_known_paywall("https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6340699")
    assert _is_known_paywall("https://academic.oup.com/milmed/article/191/3-4/e522")
    assert _is_known_paywall("https://www.sciencedirect.com/article/pii/S0360835226001208")


def test_non_paywall_urls_pass():
    """Defence-relevant news sources must NOT be caught."""
    from aria_service.intel.researcher import _is_known_paywall
    assert not _is_known_paywall("https://www.reuters.com/world/article")
    assert not _is_known_paywall("https://defenceweb.co.za/feed/")
    assert not _is_known_paywall("https://www.al-monitor.com/originals/2026/04/...")
