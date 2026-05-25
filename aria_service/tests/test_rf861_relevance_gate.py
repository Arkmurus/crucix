"""R-F861 — content relevance gate on hypothesis-validation evidence (FIX 6).

validate_hypothesis searches academic backends (crossref/openalex) on the
hypothesis keywords. Those resolve keyword collisions to off-topic papers that
pass the domain gate but whose CONTENT is irrelevant (live: an arts journal
matched "offset", an IPO-underpricing paper matched "forward" for a Finland
F-35 offset hypothesis). Those got deep-read + fed to the LLM as "evidence",
wasting deep-read budget + encode load and diluting the verdict.

R-F861 requires a defence anchor in title+body before an article is used as
evidence — reusing the existing _has_defence_anchor gate (no new encodes).
"""
from __future__ import annotations

from aria_service.intel.researcher import _has_defence_anchor


def test_drops_keyword_collision_junk():
    # The exact failure class from the live logs — no defence content.
    assert _has_defence_anchor("Offset printing techniques in modern art journals") is False
    assert _has_defence_anchor("IPO underpricing and forward returns in emerging markets") is False
    assert _has_defence_anchor("Beeswax in Nok pots: early West African honey use") is False


def test_keeps_genuine_defence_content():
    assert _has_defence_anchor("Finland F-35 offset agreement with Patria — defence procurement") is True
    assert _has_defence_anchor("Naval frigate tender awarded; military export licence") is True
    assert _has_defence_anchor("NATO air force UAV deployment in the Baltic") is True


def test_gate_wired_into_validate_hypothesis():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "intel" / "researcher.py").read_text(encoding="utf-8")
    # The relevance gate + the no-relevant-evidence exit must both be present in
    # the validate_hypothesis evidence loop.
    assert "R-F861" in src
    assert "NO_RELEVANT_EVIDENCE" in src
    # The gate calls _has_defence_anchor on the fetched body, not just the domain.
    assert 'if not _has_defence_anchor(f"{a.get(\'title\', \'\')} {body[:1500]}"):' in src
