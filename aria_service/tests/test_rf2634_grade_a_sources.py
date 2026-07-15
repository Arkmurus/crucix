"""R-F2634 — Grade-A source rebuild: ARIA's authoritative tier_1b layer was ~gone.

THE FINDING (probed live 2026-07-15, all 76 configured feeds, same 15s contract
poll_feeds uses):
    76 feeds | 30 OK | 46 DEAD (61%)
    404 x21 · 403 x9 · NOT_XML x9 · DNS_DEAD x2 · TIMEOUT x2 · 401/400/503 x1 each
and the dead ones are the AUTHORITATIVE layer — ~18 of the 20 tier_1b feeds:
    Janes x9 (404), RUSI (404), CSIS (404), FT Defence (404), Reuters Defence (401),
    IISS/Chatham House/Atlantic Council/Bloomberg (403), Carnegie (NOT_XML).

WHY they are dead is the point: Janes/FT/Bloomberg/Reuters are PAYWALLED commercial
products that killed free RSS deliberately; the think tanks are Cloudflare-blocked.
Chasing them is fighting the vendor. Decision-grade intel wants OFFICIAL PRIMARY
sources — free, stable, and tier_1a (HIGHER authority than the tier_1b we lost).

WHY IT MATTERS (USP): ARIA sells honest, corroborated, decision-grade intel.
Corroboration needs MULTIPLE INDEPENDENT LIVE sources on the same event. With the
tier_1a/1b layer dead, the surviving pool is mostly tier_2 news — which cannot
produce decision-grade corroboration. This is the root of "not Grade A".

EVERY added feed was PROBED and returned 200 + real XML + >0 items. 20 of 34
candidates were REJECTED (NATO, SIPRI, EDA, OFAC, State Dept, RUSI, CSIS, Janes-alt,
EU Council, Lawfare, ...). Nothing unverified ships — that is the whole discipline.

NOT in this change (deliberate): removing the 46 dead feeds. My probe runs from a
different network/IP than the fly box, so a 403 here may not be a 403 there (§22 —
my probe is not the box's reality). R-F2630 now makes the box's OWN failed_feeds
nameable; remove on THAT evidence, in a second pass.
"""
import collections

from aria_service.intel.news_monitor import NEWS_SOURCES

# The 14 feeds verified working on 2026-07-15 (200 + XML + items).
_EXPECTED_NEW = {
    # tier_1a OFFICIAL / PRIMARY.
    # NB: "US DoD Releases" is deliberately ABSENT — it resolves to the SAME URL as the
    # pre-existing "US DoD Daily Contracts". test_rf2634_no_duplicate_feed_urls caught
    # that during this change: two names on one URL count one article twice =>
    # evidence_count=2 => FALSE corroboration. The existing entry was re-tiered to
    # tier_1a instead.
    "US DoD News", "US Army News",
    "UK MOD Announcements", "UN News Peace and Security", "UN News",
    # tier_1b specialist outlets that still publish free RSS.
    # NB: Breaking Defense / War on the Rocks / Defence Blog are deliberately ABSENT —
    # they were ALREADY registered and healthy (they probed OK precisely because they
    # already work). Re-adding them would put two names on one URL => one article
    # counted twice => FALSE corroboration. Caught by the duplicate test below.
    "Defense News", "The War Zone", "DefenseScoop", "Bellingcat", "Crisis Group",
}


def _by_name():
    return {n: (n, u, c, l, t, tp) for (n, u, c, l, t, tp) in NEWS_SOURCES}


def test_rf2634_verified_sources_are_registered():
    """THE CAPABILITY TEST — the Grade-A sources must actually be in the feed list."""
    names = set(_by_name())
    missing = sorted(_EXPECTED_NEW - names)
    assert not missing, (
        "Grade-A verified sources are NOT registered, so ARIA still polls a feed list "
        f"whose authoritative layer is dead: {missing}"
    )


def test_rf2634_tier_1a_primary_layer_exists():
    """ARIA had ZERO tier_1a feeds — only tier_1b (20, ~18 dead) and tier_2 (56).

    Decision-grade corroboration needs official primary sources. Without a tier_1a
    layer the best ARIA can ever say is 'tier_2 news said so'.
    """
    tiers = collections.Counter(t for (_n, _u, _c, _l, t, _tp) in NEWS_SOURCES)
    assert tiers.get("tier_1a", 0) >= 6, (
        f"no meaningful tier_1a (official/primary) layer: {dict(tiers)} — "
        "corroboration cannot reach decision-grade on tier_2 news alone"
    )


def test_rf2634_added_sources_are_independent_orgs():
    """Corroboration counts INDEPENDENT sources (verified_intel.SourceIndependenceChecker).

    Adding 5 feeds from one org is NOT corroboration fuel. The added set must span
    multiple distinct organisations/domains.
    """
    import urllib.parse
    added = [v for k, v in _by_name().items() if k in _EXPECTED_NEW]
    domains = {urllib.parse.urlparse(u).netloc.lower().replace("www.", "") for (_n, u, _c, _l, _t, _tp) in added}
    assert len(domains) >= 8, (
        f"added feeds span only {len(domains)} domains — too concentrated to corroborate: {sorted(domains)}"
    )


def test_rf2634_tuple_contract_is_intact():
    """NON-REGRESSION: every source must keep the (name,url,category,lang,tier,topics)
    contract poll_feeds unpacks — a malformed tuple would break the WHOLE poll."""
    valid_tiers = {"tier_1a", "tier_1b", "tier_2"}
    for s in NEWS_SOURCES:
        assert len(s) == 6, f"malformed source tuple (poll_feeds unpacks 6): {s}"
        name, url, category, lang, tier, topics = s
        assert isinstance(name, str) and name, f"bad name: {s}"
        assert isinstance(url, str) and url.startswith("http"), f"bad url: {name} -> {url}"
        assert isinstance(category, str) and category, f"bad category: {name}"
        assert isinstance(lang, str) and lang, f"bad lang: {name}"
        assert tier in valid_tiers, f"bad tier for {name}: {tier}"
        assert isinstance(topics, list), f"topics must be a list: {name}"


def test_rf2634_no_duplicate_feed_urls():
    """A duplicated URL would double-count as 'two sources' and manufacture FALSE
    corroboration — the exact never-false-clean failure this work exists to prevent."""
    urls = [u.rstrip("/") for (_n, u, _c, _l, _t, _tp) in NEWS_SOURCES]
    dupes = [u for u, c in collections.Counter(urls).items() if c > 1]
    assert not dupes, f"duplicate feed URLs would fake corroboration: {dupes}"
