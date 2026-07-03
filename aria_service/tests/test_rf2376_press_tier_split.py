"""R-F2376 — press-tier split honesty in dd_schema.structured_view().

BUG: the press-coverage headline metric in structured_view() counted only
T1/T2 (plus DEAD keys OFFICIAL/INDUSTRY that no backend ever writes into
source_tier_breakdown). The runtime classifier web_explorer._classify_tier
emits T1/T2/T3/T4/UNVERIFIED, and dd_orchestrator adds ENTITY_SITE/MEMORY_ONLY.
So T3 quality press (Reuters/BBC/FT/WSJ/AP) and T4 social were counted in
NEITHER bucket and VANISHED from the headline — e.g. "8 Reuters (T3) + 2 T1"
rendered "2 verified / 0 unverified" while the tier line beside it showed
T1:2, T3:8 (self-contradictory, understating reputable adverse media).

FIX (render-only): T1/T2 = verified, T3 = quality press (shown distinctly),
unverified = remainder of the breakdown (UNVERIFIED + T4 + any unmapped key).

This capability test drives the REAL structured_view() function (the broken
path) and asserts the user-visible headline, not a helper.
"""
import json

from aria_service.intel.dd_schema import structured_view


def _digital_press_value(sv: dict) -> str:
    """Pull the rendered 'Press coverage' highlight value out of the real
    structured_view output."""
    dig = [s for s in sv["sections"] if s.get("key") == "digital"][0]
    return next(h["value"] for h in dig["highlights"] if h["label"] == "Press coverage")


def test_rf2376_t3_quality_press_is_not_dropped():
    """The reported failing shape: 2 T1 + 8 T3 + 1 UNVERIFIED. The old code
    rendered '2 verified / 0 unverified', HIDING the 8 T3. The fix must surface
    T3 as quality press and must NOT show a bogus '0 unverified' that swallows
    reputable coverage."""
    tb = {"T1": 2, "T3": 8, "UNVERIFIED": 1}
    press_total = sum(tb.values())  # 11
    r = {
        "digital": {
            "press_coverage": [{"url": f"https://x/{i}"} for i in range(press_total)],
            "source_tier_breakdown": tb,
        },
    }
    sv = structured_view(r)
    value = _digital_press_value(sv)

    # (1) T3 quality press is represented in the headline, not vanished.
    assert "quality press" in value, f"T3 not surfaced: {value}"
    assert "8 quality press" in value, f"T3 count wrong/hidden: {value}"

    # (2) The old self-contradictory "2 verified / 0 unverified" (hiding 8 T3)
    #     must NOT be produced.
    assert value != "2 verified / 0 unverified", value
    assert "0 unverified" not in value, f"still hiding tiers as 0 unverified: {value}"

    # (3) The split buckets must SUM to the press total — so no tier can be
    #     silently dropped again.
    verified = 2          # T1 + T2
    quality_press = 8     # T3
    unverified = 1        # UNVERIFIED + T4 remainder
    assert verified + quality_press + unverified == press_total

    # And the rendered numbers match that split.
    assert value == "2 verified / 8 quality press / 1 unverified", value


def test_rf2376_t4_social_folds_into_unverified():
    """T4 (social: twitter/x/facebook/linkedin/instagram) is ~unverified and
    must be counted, not dropped."""
    tb = {"T1": 1, "T4": 3, "UNVERIFIED": 2}
    press_total = sum(tb.values())  # 6
    r = {
        "digital": {
            "press_coverage": [{"url": f"https://x/{i}"} for i in range(press_total)],
            "source_tier_breakdown": tb,
        },
    }
    sv = structured_view(r)
    value = _digital_press_value(sv)
    # verified=1 (T1), quality_press=0 (omitted), unverified = 3(T4)+2 = 5
    assert value == "1 verified / 5 unverified", value


def test_rf2376_dead_official_industry_keys_are_gone():
    """OFFICIAL/INDUSTRY are never emitted into source_tier_breakdown. Even if a
    stray legacy key appears, the remainder logic must absorb it into unverified
    rather than silently vanish it (no tier dropped)."""
    tb = {"T2": 2, "OFFICIAL": 4}  # OFFICIAL is a dead/unknown key
    press_total = sum(tb.values())  # 6
    r = {
        "digital": {
            "press_coverage": [{"url": f"https://x/{i}"} for i in range(press_total)],
            "source_tier_breakdown": tb,
        },
    }
    sv = structured_view(r)
    value = _digital_press_value(sv)
    # T2 -> verified(2); unknown OFFICIAL(4) folds into unverified remainder.
    # Nothing vanishes: 2 + 0 + 4 == 6.
    assert value == "2 verified / 4 unverified", value
    verified, quality_press, unverified = 2, 0, 4
    assert verified + quality_press + unverified == press_total


def test_rf2376_evidence_array_untouched():
    """RENDER-ONLY: the fix changes only the headline metric — the evidence
    array (underlying cited articles) must be preserved."""
    r = {
        "digital": {
            "press_coverage": [
                {"url": "https://reuters.com/a", "source": "Reuters",
                 "source_tier": "T3", "snippet": "adverse item"},
                {"url": "https://ofac.treasury.gov/b", "source": "OFAC",
                 "source_tier": "T1", "snippet": "listing"},
            ],
            "source_tier_breakdown": {"T3": 1, "T1": 1},
        },
    }
    sv = structured_view(r)
    dig = [s for s in sv["sections"] if s.get("key") == "digital"][0]
    ev = dig.get("evidence") or []
    assert len(ev) == 2, ev
    urls = {e["url"] for e in ev}
    assert "https://reuters.com/a" in urls and "https://ofac.treasury.gov/b" in urls


def test_rf2376_does_not_mutate_stored_report_fields():
    """structured_view is a render contract — it must not mutate the input
    report's stored fields (source_tier_breakdown / press_coverage)."""
    tb = {"T1": 2, "T3": 8, "UNVERIFIED": 1}
    r = {
        "digital": {
            "press_coverage": [{"url": f"https://x/{i}"} for i in range(11)],
            "source_tier_breakdown": tb,
        },
    }
    before = json.dumps(r, sort_keys=True)
    structured_view(r)
    after = json.dumps(r, sort_keys=True)
    assert before == after, "structured_view mutated the stored report dict"
