"""R-F2008 — signal_correlator must not silently drop gate-passing clusters.

Found by the end-to-end chain test (news -> ledger -> correlate -> opportunities):
a country with budget_increase + active_tender (score 7.0, 2 types) passed the
score>=5 / >=2-type gate in correlate_signals() but _generate_insight returned
None for it (no minister/contact/competitor/4+ signals) -> 0 insights -> nothing
reached opportunities/BD. News signals (mostly tender/budget/conflict) almost
never include a contact, so this starved the whole chain.

These call the REAL _generate_insight.
"""
from aria_service.intel import signal_correlator as sc


def _sig(t):
    return {"type": t, "weight": sc.SIGNAL_WEIGHTS.get(t, 1.0), "text": f"{t} headline", "source": "test"}


def test_budget_plus_tender_without_contact_emits_opportunity_window():
    sigs = [_sig("budget_increase"), _sig("active_tender")]
    r = sc._generate_insight("angola", sigs, {"budget_increase", "active_tender"}, 7.0)
    assert r is not None, "budget+tender must NOT be dropped (was the chain break)"
    assert r["insight_type"] == "OPPORTUNITY_WINDOW"
    assert r["country"] == "Angola" and r["signal_count"] == 2


def test_generic_two_type_cluster_is_surfaced_not_none():
    sigs = [_sig("defence_news"), _sig("conflict_escalation")]
    r = sc._generate_insight("kenya", sigs, {"defence_news", "conflict_escalation"}, 5.5)
    assert r is not None, "any gate-passing cluster must emit an insight"
    assert r["insight_type"] == "MARKET_SIGNAL"


def test_full_multidimensional_window_still_classifies_specifically():
    sigs = [_sig("budget_increase"), _sig("active_tender"), _sig("warm_contact")]
    r = sc._generate_insight(
        "mozambique", sigs, {"budget_increase", "active_tender", "warm_contact"}, 9.0)
    assert r["insight_type"] == "OPPORTUNITY_WINDOW"  # specific branch unchanged


def test_competitive_vacuum_branch_unchanged():
    sigs = [_sig("active_tender"), _sig("warm_contact")]
    r = sc._generate_insight("ghana", sigs, {"active_tender", "warm_contact"}, 6.0)
    assert r["insight_type"] == "COMPETITIVE_VACUUM"  # pre-existing path intact
