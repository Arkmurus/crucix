"""Capability test: legal/regulatory articles pass the research pipeline.

R-F1525: the research pipeline's anchor gate and scoring step must accept
legal articles (sanctions, export control, trade law, arbitration) so they
compete for deep-reading slots alongside defence articles.

This test drives the actual _has_defence_anchor function and the scoring
logic from research_and_learn to prove legal content is not silently dropped.
"""
import pytest
from aria_service.intel.researcher import (
    _has_defence_anchor,
    _DEFENCE_ANCHOR_SUBSTRINGS,
    LEGAL_FEEDS,
    RESEARCH_FEEDS,
)


class TestLegalAnchorGate:
    """The anchor gate must pass legal/regulatory article titles."""

    @pytest.mark.parametrize("title,description", [
        # Sanctions
        ("OFAC sanctions enforcement action 2026", "Treasury imposes sanctions on entities"),
        ("EU sanctions regime Russia Iran 2026", "Council adopts new restrictive measures"),
        ("UK sanctions notice", "OFSI publishes general licence amendment"),
        ("UN Security Council sanctions committee", "North Korea sanctions update"),

        # Export controls
        ("BIS EAR export control reform 2026", "Bureau of Industry and Security rulemaking"),
        ("UK ECJU open general export licence update", "Strategic export controls amended"),
        ("Dual-use export control regulation", "EU adopts new dual-use regulation"),

        # Trade law
        ("WTO dispute settlement ruling 2026", "Appellate body issues report"),
        ("Anti-dumping investigation opened", "Trade remedy investigation launched"),
        ("Countervailing duty determination", "Commerce department preliminary finding"),

        # Arbitration & contract law
        ("ICC arbitration award enforcement", "International Chamber of Commerce ruling"),
        ("Force majeure clause dispute", "Contract law arbitration proceeding"),
        ("Investment treaty arbitration", "Bilateral investment treaty claim"),

        # Regulatory compliance
        ("AML compliance regulation update", "Anti-money laundering directive"),
        ("GDPR enforcement action", "Data protection authority fine"),
        ("Antitrust merger control review", "Competition law investigation"),
    ])
    def test_legal_title_passes_anchor(self, title, description):
        text = f"{title} {description}"
        assert _has_defence_anchor(text), (
            f"Legal article should pass anchor gate: {title}"
        )

    def test_defence_articles_still_pass(self):
        """Defence articles must still pass — regression check."""
        defence_titles = [
            "Fighter jet procurement tender 2026",
            "Naval frigate contract awarded",
            "Missile defence system deal signed",
            "Armoured vehicle IFV tender Africa",
            "Drone export agreement delivered",
        ]
        for title in defence_titles:
            assert _has_defence_anchor(title), (
                f"Defence article should still pass: {title}"
            )

    def test_off_topic_articles_still_blocked(self):
        """Non-defence/non-legal articles must still be blocked."""
        off_topic = [
            "Hotel billion-dollar deal in Angola",
            "Tourism award ceremony 2026",
            "Entertainment contract signed",
            "Real estate development project",
            "Agricultural commodity prices",
        ]
        for title in off_topic:
            assert not _has_defence_anchor(title), (
                f"Off-topic article should be blocked: {title}"
            )


class TestLegalFeedsConfigured:
    """LEGAL_FEEDS must be populated with working feeds."""

    def test_legal_feeds_not_empty(self):
        assert len(LEGAL_FEEDS) > 0, "LEGAL_FEEDS must have at least one feed"

    def test_legal_feeds_have_required_keys(self):
        for feed in LEGAL_FEEDS:
            assert "name" in feed, f"Feed missing name: {feed}"
            assert "url" in feed, f"Feed missing url: {feed}"
            assert "category" in feed, f"Feed missing category: {feed}"
            assert feed["category"] in (
                "sanctions_law", "export_control", "trade_law",
                "contract_law", "swiss_law", "uae_law", "eu_law",
                "international_law",
            ), f"Invalid category: {feed['category']}"

    def test_legal_feeds_have_valid_urls(self):
        for feed in LEGAL_FEEDS:
            assert feed["url"].startswith("http"), (
                f"Invalid URL for {feed['name']}: {feed['url']}"
            )

    def test_research_feeds_unchanged(self):
        """RESEARCH_FEEDS must still have the original 34 defence feeds."""
        assert len(RESEARCH_FEEDS) == 34, (
            f"Expected 34 RESEARCH_FEEDS, got {len(RESEARCH_FEEDS)}"
        )


class TestLegalAnchorTerms:
    """The anchor term list must include legal/regulatory terms."""

    def test_legal_terms_present(self):
        legal_terms = [
            "export control", "sanctions", "embargo", "dual-use",
            "ofac", "ear", "itar",
            "trade law", "trade remedy", "anti-dumping",
            "arbitration", "compliance", "regulation",
            "anti-money laundering", "aml",
            "data protection", "gdpr",
            "competition law", "antitrust",
        ]
        substr_lower = [s.lower() for s in _DEFENCE_ANCHOR_SUBSTRINGS]
        for term in legal_terms:
            found = any(term in s for s in substr_lower)
            assert found, f"Legal term missing from anchors: {term}"
