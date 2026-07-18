"""R-F2714 — formal intel_grade A/B/C/REJECT for channel intelligence.

The channel pipeline had no grade model: a single-source Tier-2 article could be
labelled "decision-grade", and the Node selector gated on a customer_value score
that is NEVER computed (absent → 0 vs a >=80 gate), so the raw-news lane was
structurally unpublishable. This introduces a formal grade derived from evidence
signals that already exist (tier, corroboration, evidence URL, named entity,
relevance) — the authority the Telegram selector gates on.

USP: an OFFICIAL primary source (OFAC/gov.uk) is Grade A even single-source;
a Tier-2 press single-source is Grade B and must be labelled corroboration-pending;
context/no-evidence is REJECT and never publishes.
"""
from __future__ import annotations

from aria_service.intel.news_monitor import _compute_intel_grade, _build_intel_signal


def _g(**k):
    return _compute_intel_grade(**k)[0]


def test_rf2714_grade_A_official_or_corroborated():
    # Official primary single-source (OFAC designation) → A
    assert _g(source_tier="tier_1a", signal_type="sanctions_change", priority="HIGH",
              evidence_count=1, url="https://ofac.treasury.gov/x", entities={"countries": ["Iran"]}) == "A"
    # Official tender single-source → A
    assert _g(source_tier="tier_1b", signal_type="active_tender", priority="HIGH",
              evidence_count=1, url="https://gov.uk/x", entities={"countries": ["UK"]}) == "A"
    # Corroborated (>=2 sources) tier_2 HIGH → A
    assert _g(source_tier="tier_2", signal_type="contract_award", priority="HIGH",
              evidence_count=2, url="https://news.example/x", entities={"oems": ["BAE"]}) == "A"


def test_rf2714_grade_B_single_credible_source():
    # Tier-2 single-source, high relevance → B (corroboration pending, publish only labelled)
    assert _g(source_tier="tier_2", signal_type="active_tender", priority="HIGH",
              evidence_count=1, url="https://news.example/x", entities={"countries": ["UK"]}) == "B"
    # Tier-1b at MEDIUM relevance → B
    assert _g(source_tier="tier_1b", signal_type="budget_movement", priority="MEDIUM",
              evidence_count=1, url="https://gov.uk/y", entities={"oems": ["Thales"]}) == "B"


def test_rf2714_grade_C_weak():
    assert _g(source_tier="tier_3", signal_type="budget_movement", priority="MEDIUM",
              evidence_count=1, url="https://blog.example/x", entities={"oems": ["BAE"]}) == "C"


def test_rf2714_reject_context_no_url_no_entity():
    # context/situational → REJECT
    assert _g(source_tier="tier_1a", signal_type="situational_awareness", priority="LOW",
              evidence_count=1, url="https://gov.uk/x", entities={"countries": ["UK"]}) == "REJECT"
    # no evidence URL → REJECT even for tier_1a corroborated
    assert _g(source_tier="tier_1a", signal_type="active_tender", priority="HIGH",
              evidence_count=2, url="", entities={"countries": ["UK"]}) == "REJECT"
    # no named entity → REJECT
    assert _g(source_tier="tier_1a", signal_type="active_tender", priority="HIGH",
              evidence_count=2, url="https://gov.uk/x", entities={}) == "REJECT"


def test_rf2714_A_beats_B_ordering():
    """The grade ladder is strict: A is only ever official/corroborated; a purely
    single-source Tier-2 can never reach A (that would be the old overclaim)."""
    a = _g(source_tier="tier_1a", signal_type="sanctions_change", priority="HIGH",
           evidence_count=1, url="https://ofac.treasury.gov/x", entities={"countries": ["Iran"]})
    b = _g(source_tier="tier_2", signal_type="sanctions_change", priority="HIGH",
           evidence_count=1, url="https://news.example/x", entities={"countries": ["Iran"]})
    assert a == "A" and b == "B"


def test_rf2714_build_intel_signal_attaches_grade():
    """The real promotion path attaches intel_grade + grade_reason."""
    sig = _build_intel_signal({
        "title": "UK MoD awards frigate contract to BAE",
        "source": "gov.uk", "tier": "tier_1a", "category": "procurement",
        "url": "https://gov.uk/contract/123", "evidence_count": 2,
        "full_text": "The UK Ministry of Defence awarded a contract to BAE Systems for frigates.",
    })
    assert "intel_grade" in sig and sig["intel_grade"] in ("A", "B", "C", "REJECT")
    assert isinstance(sig.get("grade_reason"), str) and sig["grade_reason"]
