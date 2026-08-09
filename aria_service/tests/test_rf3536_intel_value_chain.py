"""R-F3536 — the intel value chain: what ARIA claims, grades and publishes.

Everything here was reproduced against LIVE production data before it was fixed.

Dashboard, 2026-07-31, four Grade-A "actionable" tenders shown to the customer:

  Italy   -> communication_systems   because "radio" is inside "radiological"
  Czechia -> surveillance_systems    because "sensor" is inside "Sensors"

The Italian tender is ionising-radiation PPE for a Sicilian hospital trust; the
Czech one is electrical current sensors for the national grid operator. Two of
four Grade-A items told an analyst to assess a bid on hospital PPE and grid
hardware.

Golden feed, same day, 100 signals:

  Grade A: natural_hazard 46 | active_tender 8 | conflict_escalation 2
  Grade A: sanctions_change 0

82% of the decision-grade pool was weather — which the channel is not allowed to
publish — so procurement won the channel by attrition, not by editorial choice.
"""

from __future__ import annotations

import pathlib

import pytest

from aria_service.intel.tender_monitor import (
    WEAK_KEYWORDS,
    match_product_categories,
)
from aria_service.intel.news_monitor import _compute_intel_grade
from aria_service.intel.signal_correlator import _independent_origins


_REPO = pathlib.Path(__file__).resolve().parents[2]


# ── The classifier: a wrong category is worse than none ──────────────────────


@pytest.mark.parametrize("label,text,cpv", [
    ("radiological PPE, Sicilian hospital trust",
     "Nuclear, biological, chemical and radiological protection equipment "
     "Fornitura Dispositivi di Protezione Individuale protezione dalle radiazioni ionizzanti",
     ["33735100"]),
    ("electrical current sensors, national grid operator",
     "Sensors DODAVKY PROUDOVYCH SENZORU current sensors CEZ Distribuce electricity distribution",
     ["38000000"]),
])
def test_live_false_positives_no_longer_assert_a_product_line(label, text, cpv):
    assert match_product_categories(text, cpv) == [], (
        f"{label}: still classified as a defence product line"
    )


def test_radio_does_not_match_radiological():
    """The exact substring defect, isolated."""
    assert match_product_categories("radiological protection", ["35000000"]) == []
    assert "communication_systems" in match_product_categories(
        "supply of tactical radio equipment", ["35000000"])


def test_genuine_defence_tenders_still_classify():
    assert "surveillance_systems" in match_product_categories(
        "Surveillance and security systems and devices, signal-blocking equipment", ["35120000"])
    ammo = match_product_categories("Supply of 5.56mm small arms ammunition and mortar rounds", ["35330000"])
    assert "ammunition" in ammo
    assert "armoured_vehicles" in match_product_categories(
        "Military vehicles and associated parts", ["35410000"])


def test_a_weak_term_needs_a_defence_context_to_count():
    """"sensor" in a defence tender is a sensor; in a grid tender it is not."""
    assert match_product_categories("supply of sensors", ["38000000"]) == []
    assert "surveillance_systems" in match_product_categories("supply of sensors", ["35000000"])


def test_a_weak_term_also_counts_alongside_a_strong_one():
    """No CPV, but "mrap" makes the context unambiguous."""
    got = match_product_categories("MRAP protected vehicles with thermal imaging sensors", [])
    assert "armoured_vehicles" in got
    assert "surveillance_systems" in got


def test_weak_list_covers_the_terms_that_actually_misfired():
    for term in ("radio", "sensor", "surveillance", "shell", "spare parts"):
        assert term in WEAK_KEYWORDS, f"{term!r} is generic and must not assert a category alone"


def test_plural_and_spacing_variants_still_match():
    assert "patrol_boats" in match_product_categories("procurement of patrol   boats", ["35000000"])


# ── Grading: decision-grade must mean a decision ─────────────────────────────


def _grade(**kw):
    base = dict(
        source_tier="tier_1a", signal_type="natural_hazard", priority="HIGH",
        evidence_count=1, url="https://earthquake.usgs.gov/x",
        entities={"countries": ["TR"]},
    )
    base.update(kw)
    return _compute_intel_grade(**base)


def test_an_earthquake_with_no_portfolio_nexus_is_not_decision_grade():
    grade, reason = _grade()
    assert grade != "A", "official + HIGH still buys Grade A for ambient weather"
    assert grade == "B"
    assert "nexus" in reason


def test_the_same_hazard_becomes_decision_grade_when_it_names_what_it_hits():
    grade, _ = _grade(entities={"countries": ["TR"], "oems": ["Aselsan"]})
    assert grade == "A", "a hazard naming an affected supplier IS a decision"


def test_the_grader_takes_no_argument_nothing_can_supply():
    """R-F3544 — R-F3536 added a `portfolio_nexus` flag that no caller ever set.

    Declared and consumed inside one function, with `_has_specific_nexus` doing
    all the work: the same producer-with-no-carrier defect being closed elsewhere
    this session, introduced by the fix for it. Removed rather than given a
    fabricated caller, and it could not have been honest anyway — this grade is
    computed once and served to every tenant, while "is it in MY portfolio" is
    per-user, and belongs on the dashboard where the caller's watchlist is known.
    """
    import inspect
    params = set(inspect.signature(_compute_intel_grade).parameters)
    assert "portfolio_nexus" not in params, (
        "a grading input exists that no caller supplies"
    )
    # and the nexus rule that DOES work is still enforced
    assert _grade(entities={"countries": ["TR"], "oems": ["Aselsan"]})[0] == "A"
    assert _grade(entities={"countries": ["TR"]})[0] == "B"


def test_a_sanctions_designation_is_still_grade_a_on_source_authority():
    """The lane this whole change exists to unblock must not regress."""
    grade, _ = _compute_intel_grade(
        source_tier="tier_1a", signal_type="sanctions_change", priority="HIGH",
        evidence_count=1, url="https://sanctionssearch.ofac.treas.gov/",
        entities={"countries": ["RU"]},
    )
    assert grade == "A"


def test_conflict_escalation_is_not_treated_as_ambient():
    grade, _ = _compute_intel_grade(
        source_tier="tier_1a", signal_type="conflict_escalation", priority="HIGH",
        evidence_count=2, url="https://example.gov/x", entities={"countries": ["YE"]},
    )
    assert grade == "A"


def test_the_honesty_floor_is_untouched():
    assert _grade(url="")[0] == "REJECT"
    assert _grade(entities={})[0] == "REJECT"
    assert _grade(priority="LOW")[0] == "REJECT"


# ── Corroboration: two aggregators are not two witnesses ─────────────────────


def test_two_propaganda_channels_are_one_origin():
    got = _independent_origins([
        {"source": "intelslava", "url": "telegram:intelslava/1"},
        {"source": "wartranslated", "url": "telegram:wartranslated/2"},
    ])
    assert got == 1, "two state-aligned aggregators were counted as corroboration"


def test_a_propaganda_channel_never_corroborates_a_real_publisher():
    got = _independent_origins([
        {"source": "intelslava", "url": "telegram:intelslava/1"},
        {"url": "https://reuters.com/story"},
    ])
    assert got == 1, "a propaganda repost was counted as a second witness"


def test_genuinely_independent_publishers_still_count():
    got = _independent_origins([
        {"url": "https://reuters.com/a"},
        {"url": "https://apnews.com/b"},
    ])
    assert got == 2


def test_the_corroboration_rule_reuses_the_constitutional_source_list():
    """One derivation point: the list that says these are never [CONFIRMED] is the
    same list that says they never corroborate.

    R-F3547 moved the rule OUT of signal_correlator into
    dd_independent_verifier.count_independent_witnesses so the correlation cards
    and news corroboration share it — it previously lived only in the correlator,
    which is how the news grader ended up with no notion of it at all. The
    property is unchanged and now stronger; only its home moved.
    """
    verifier = (_REPO / "aria_service" / "intel" / "dd_independent_verifier.py").read_text(encoding="utf-8")
    assert "_looks_like_propaganda_source" in verifier, (
        "the shared witness count no longer consults the constitutional source list"
    )
    correlator = (_REPO / "aria_service" / "intel" / "signal_correlator.py").read_text(encoding="utf-8")
    assert "count_independent_witnesses" in correlator, (
        "the correlator no longer uses the shared rule"
    )
    assert "_looks_like_propaganda_source" not in correlator, (
        "the correlator kept its own copy — two definitions of one question"
    )


# ── Channel policy: procurement is a workflow item, not intelligence ─────────


def test_the_intel_channel_publishes_open_tenders_again():
    """R-F3810 — R-F3536's exclusion was REVERSED, by operator ruling 2026-08-09.

    This test asserted `'active_tender' not in allowed` on R-F3536's editorial line:
    an open tender is only that a buyer exists, which is a workflow item rather than
    intelligence. R-F3688 (2026-08-04) then re-added it, reading the absence as
    accidental drift from the Python taxonomy — the brain emits BOTH `active_tender`
    and `contract_award`, and only the second was listed.

    The two R-numbers genuinely contradicted each other and the test had been red
    ever since. Put to the operator with both rationales; R-F3688 stands, on its
    measured harm: the 07:00 slot held for four consecutive days reporting "no
    Grade A" while twelve fresh signals existed.

    So this now pins the CURRENT policy rather than the superseded one. Reversing it
    again is an operator decision, not a code cleanup — do not "restore" R-F3536's
    line because this docstring mentions it.
    """
    hooks = (_REPO / "lib" / "telegram" / "channelServerHooks.mjs").read_text(encoding="utf-8")
    allowed = hooks.split("_GOLDEN_ALLOWED_TYPES = new Set([", 1)[1].split("]);", 1)[0]
    assert "'active_tender'" in allowed, (
        "open tenders were dropped again — R-F3688 is the standing policy (operator "
        "ruling 2026-08-09); reversing it needs a new operator decision, not a patch"
    )
    for keep in ("'sanctions_change'", "'conflict_escalation'", "'competitor_activity'",
                 "'contract_award'", "'budget_movement'", "'programme_signal'"):
        assert keep in allowed, f"{keep} was dropped from the channel"


# ── Dashboard coherence ─────────────────────────────────────────────────────


def _dashboard() -> str:
    return (_REPO / "public" / "dashboard.html").read_text(encoding="utf-8")


def test_raw_channel_text_is_no_longer_rendered_to_customers():
    html = _dashboard()
    assert "Raw Telegram Collection" not in html
    # the verbatim post body was the thing being published
    assert "escHtml(truncate(p.text||'',150))" not in html, (
        "verbatim propaganda-tier channel text is still rendered in the product"
    )
    assert "Channel collection is an INPUT" in html, "the collection lost its honest framing"


def test_the_kpi_row_counts_the_same_rows_it_sits_above():
    html = _dashboard()
    assert "c.severity === 'critical' || c.severity === 'high'" in html, (
        "'High Correlations' still counts critical only, so 3 sits above a list of 5"
    )
    assert "window._lastFeedSignals" in html, (
        "the tender KPI still counts a different window from the feed it sits above"
    )


def test_grade_a_no_longer_prints_single_source_against_itself():
    html = _dashboard()
    assert "function evidenceLabel(s)" in html
    assert "escHtml(s.corroboration || 'single-source')" not in html, (
        "Grade A cards still print 'Evidence: single-source' under an "
        "'official primary evidence' badge"
    )
    assert "official primary source" in html


def test_an_empty_watchlist_is_reported_as_empty_not_as_no_match():
    html = _dashboard()
    assert "add entities to your watchlist" in html, (
        "a user with no watchlist is told the match failed rather than that they "
        "have not told ARIA what they care about"
    )
    assert "window._wlMatcherSize" in html


# ── R-F3540 — provenance is derived, not hand-set ────────────────────────────


def test_every_signal_is_attributable_to_the_adapter_that_produced_it():
    """69 of 100 live signals had NO attributable producer.

    `promotion_source` fell back to `finding.source_key`, which only two of the
    registered adapters set. `register_adapter(name, fn)` already knows the name
    and `promote_findings` already receives it — it simply was not carried the
    last hop into the signal, so when a bad signal reached a customer there was
    no way to tell which adapter emitted it.
    """
    from aria_service.intel.golden_intel_bridge import _normalize_finding_to_signal as norm
    finding = {
        "title": "Entity designated", "signal_type": "sanctions_change",
        "why_it_matters": "why", "recommended_action": "act",
        "evidence_url": "https://sanctionssearch.ofac.treas.gov/",
        "source": "OFAC SDN designation",
    }
    assert norm(dict(finding), source_name="sanctions_diff")["promotion_source"] == "sanctions_diff"
    # an adapter that DOES stamp its own key keeps it
    assert norm(dict(finding, source_key="explicit"), source_name="sanctions_diff")["promotion_source"] == "explicit"
    # and with neither, it still degrades to something rather than None
    assert norm(dict(finding))["promotion_source"] == "OFAC SDN designation"


def test_the_adapter_name_is_threaded_from_the_registry_to_the_signal():
    """Guard the CARRIER, not just the helper — the last hop was the defect."""
    src = (_REPO / "aria_service" / "intel" / "golden_intel_bridge.py").read_text(encoding="utf-8")
    assert "_normalize_finding_to_signal(finding, source_name=source_name)" in src, (
        "the adapter name is known at the call site and still not passed on"
    )
