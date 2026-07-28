"""R-F3372 — the chosen company must be JUSTIFIED by the payload, not merely present in it.

THE DEFECT, in code shipped hours earlier. `capture_multihop.py:50` did
`top = search[0]` — it trusted the registry's relevance ranking. R-F3367's
derivation guard could not catch this: `results[0]`'s company number IS in the
payload, so it is perfectly "derived". Derivation proves an entity was not
invented. It says nothing about whether it is the RIGHT one.

MEASURED AGAINST THE REAL REGISTRY. For precise full names the ranking is good —
8/8 correct. For the short names operators actually type, it is dangerous:

    "Chemring" -> results[0] = CHEMRING LIMITED      (DISSOLVED, 00369716)
                  the live parent CHEMRING GROUP PLC (active, 00086662) ranks 4th
    "Babcock"  -> results[0] = BABCOCK LTD           (DISSOLVED, 13786823)
    "QinetiQ"  -> results[1] = PAWSTOPURR LTD        (unrelated)
    "Cobham"   -> results[2] = CMA - PHD LIMITED     (unrelated)

Running due diligence against a dissolved shell instead of the live group is not
a near-miss; every downstream hop — officers, PSC, sanctions — is then about the
wrong company, and reported with full confidence.

WHY EXACT-TITLE MATCHING IS THE WRONG FIX. `Meggitt plc` resolves to MEGGITT
LIMITED and `Ultra Electronics Holdings plc` to ULTRA ELECTRONICS HOLDINGS
LIMITED — real companies re-registered after going private. A validator demanding
the title equal the subject would reject correct resolutions. The rule has to be
about status and name CORE, not string equality.

THE INVARIANT: the number carried into hop 2 must be the one `resolve_company`
selects from hop 1's payload — and when nothing resolves confidently, the trace
must ASK rather than guess.
"""
from __future__ import annotations

import pytest

from scripts.train import build_tooluse_corpus as B


# ── real payloads, captured from the live register ─────────────────────────

CHEMRING = [
    {"title": "CHEMRING LIMITED", "company_status": "dissolved", "company_number": "00369716"},
    {"title": "CHEMRING COUNTERMEASURES LIMITED", "company_status": "active", "company_number": "00218229"},
    {"title": "CHEMRING ENERGETICS UK LIMITED", "company_status": "active", "company_number": "SC237472"},
    {"title": "CHEMRING GROUP PLC", "company_status": "active", "company_number": "00086662"},
]

BABCOCK = [
    {"title": "BABCOCK LTD", "company_status": "dissolved", "company_number": "13786823"},
    {"title": "BABCOCK AEROSPACE LIMITED", "company_status": "active", "company_number": "03887962"},
    {"title": "BABCOCK AIRPORTS LIMITED", "company_status": "active", "company_number": "03954520"},
]

QINETIQ = [
    {"title": "QINETIQ LIMITED", "company_status": "active", "company_number": "03796233"},
    {"title": "PAWSTOPURR LTD", "company_status": "active", "company_number": "16379795"},
    {"title": "QINETIQ ESTATES LIMITED", "company_status": "active", "company_number": "04186902"},
]

SERCO = [
    {"title": "SERCO LIMITED", "company_status": "active", "company_number": "00242246"},
    {"title": "SERCO LIMITED", "company_status": "closed-on", "company_number": "NF003703"},
]

MEGGITT = [{"title": "MEGGITT LIMITED", "company_status": "active", "company_number": "00432989"}]


# ── the resolver, against real ambiguity ───────────────────────────────────

def test_dissolved_top_hit_loses_to_the_active_group():
    """The whole point: 'Chemring' must not resolve to the dissolved shell."""
    chosen, reason, ambiguous = B.resolve_company("Chemring", CHEMRING)
    assert chosen is not None
    assert chosen["company_number"] == "00086662", chosen
    assert chosen["company_status"] == "active"
    assert not ambiguous
    assert "dissolved" in reason.lower() or "active" in reason.lower(), reason


def test_abstains_when_only_a_dissolved_exact_match_exists():
    """'Babcock' has no active exact match in the payload. Guessing an unrelated
    subsidiary is worse than saying so."""
    chosen, reason, ambiguous = B.resolve_company("Babcock", BABCOCK)
    assert chosen is None, chosen
    assert reason


def test_unrelated_high_rank_result_is_not_selected():
    chosen, _r, _a = B.resolve_company("QinetiQ", QINETIQ)
    assert chosen["company_number"] == "03796233"
    assert chosen["title"] != "PAWSTOPURR LTD"


def test_closed_duplicate_does_not_beat_the_active_one():
    chosen, _r, _a = B.resolve_company("Serco", SERCO)
    assert chosen["company_number"] == "00242246"


def test_legitimate_legal_form_change_still_resolves():
    """Meggitt plc -> MEGGITT LIMITED is a real re-registration, not a mismatch.
    An exact-title rule would wrongly reject it."""
    chosen, _r, _a = B.resolve_company("Meggitt plc", MEGGITT)
    assert chosen is not None and chosen["company_number"] == "00432989"


def test_resolver_is_total_on_junk():
    for bad in (None, [], "x", [{}], [{"title": None}]):
        chosen, reason, ambiguous = B.resolve_company("Anything", bad)
        assert chosen is None and reason


def test_genuine_ambiguity_is_reported_not_guessed():
    two_actives = [
        {"title": "ACME LIMITED", "company_status": "active", "company_number": "11111111"},
        {"title": "ACME PLC", "company_status": "active", "company_number": "22222222"},
    ]
    chosen, reason, ambiguous = B.resolve_company("Acme", two_actives)
    assert ambiguous is True, (chosen, reason)


# ── the validator now enforces the SELECTION, not just derivation ──────────

def _chain(number: str):
    return B.build_multihop_trace("Chemring", [
        ("companies_house_search", {"query": "Chemring"}, {"results": CHEMRING}),
        ("companies_house_officers", {"company_number": number},
         {"company_number": number, "officers": [{"name": "DOE, Jane", "resigned_on": None}]}),
    ])


def test_correctly_resolved_chain_validates():
    assert B.validate_trace(_chain("00086662")) == []


def test_chain_that_followed_results0_is_rejected():
    """This is the exact bug: the dissolved number IS in the payload, so
    derivation passes. Selection must fail it."""
    errs = B.validate_trace(_chain("00369716"))
    assert errs, "a chain that ran DD on a dissolved company was accepted"
    assert any("00369716" in e or "resolve" in e.lower() or "dissolved" in e.lower()
               for e in errs), errs


def test_unrelated_company_in_payload_is_rejected():
    t = B.build_multihop_trace("QinetiQ", [
        ("companies_house_search", {"query": "QinetiQ"}, {"results": QINETIQ}),
        ("companies_house_officers", {"company_number": "16379795"},
         {"company_number": "16379795", "officers": [{"name": "X", "resigned_on": None}]}),
    ])
    assert B.validate_trace(t), "PAWSTOPURR LTD was accepted as QinetiQ"


# ── the ambiguity trace: ask, do not guess ─────────────────────────────────

def test_ambiguity_trace_asks_instead_of_screening():
    t = B.build_resolution_trace("Babcock", {"results": BABCOCK})
    assert t["label"] == "tooluse_resolution"
    assert B.validate_trace(t) == [], B.validate_trace(t)
    final = t["messages"][-1]["content"].lower()
    assert "?" in t["messages"][-1]["content"] or "which" in final, final
    # it must NOT have proceeded to screen anything
    names = [m.get("name") for m in t["messages"] if m.get("role") == "tool"]
    assert "screen" not in names, names


def test_resolution_trace_names_the_candidates_it_rejected():
    t = B.build_resolution_trace("Babcock", {"results": BABCOCK})
    final = t["messages"][-1]["content"]
    assert "BABCOCK LTD" in final and "dissolved" in final.lower()


def test_confident_resolution_trace_proceeds():
    t = B.build_resolution_trace("Chemring", {"results": CHEMRING})
    assert B.validate_trace(t) == []
    assert "00086662" in t["messages"][-1]["content"]


# ── no regression on the three shipped corpora ────────────────────────────

def test_prior_corpora_still_validate():
    single = B.build_trace("Tesco plc", {
        "result": "CLEAR", "status": "CLEAR",
        "sanctions": {"matched": False, "matches": [], "verdict": "CLEAR"}})
    assert B.validate_trace(single) == []
    ch = B.build_challenge_trace("Tesco plc", {
        "result": "CLEAR", "status": "CLEAR",
        "sanctions": {"matched": False, "matches": [], "verdict": "CLEAR"}}, premise="clean")
    assert B.validate_trace(ch) == []


# ── tiered matching (found by running the real capture) ───────────────────
# Suffix-stripping alone made "Babcock International Group plc" ambiguous
# against BABCOCK INTERNATIONAL LIMITED, because both cores collapse to
# "babcock international". But the subject matches one title EXACTLY. An exact
# full-title match must win outright; core matching is only the fallback that
# lets Meggitt plc -> MEGGITT LIMITED still resolve.

BABCOCK_REAL = [
    {"title": "BABCOCK INTERNATIONAL GROUP PLC", "company_status": "active", "company_number": "02342138"},
    {"title": "BABCOCK INTERNATIONAL LIMITED", "company_status": "active", "company_number": "00062997"},
]

QINETIQ_REAL = [
    {"title": "QINETIQ GROUP PLC", "company_status": "active", "company_number": "04586941"},
    {"title": "QINETIQ HOLDINGS LIMITED", "company_status": "active", "company_number": "04586934"},
    {"title": "QINETIQ GROUP HOLDINGS LIMITED", "company_status": "active", "company_number": "12345678"},
]


def test_exact_title_match_wins_over_core_ambiguity():
    chosen, reason, ambiguous = B.resolve_company("Babcock International Group plc", BABCOCK_REAL)
    assert not ambiguous, reason
    assert chosen["company_number"] == "02342138", chosen


def test_exact_title_match_also_disambiguates_qinetiq():
    chosen, reason, ambiguous = B.resolve_company("QinetiQ Group plc", QINETIQ_REAL)
    assert not ambiguous, reason
    assert chosen["company_number"] == "04586941", chosen


def test_core_fallback_still_resolves_a_legal_form_change():
    """No exact title match exists for Meggitt plc -> MEGGITT LIMITED, so the
    fallback must still work. Tiering must not break the case it protects."""
    chosen, _r, ambiguous = B.resolve_company("Meggitt plc", MEGGITT)
    assert chosen is not None and not ambiguous


def test_genuine_core_ambiguity_is_still_reported():
    two = [
        {"title": "ACME HOLDINGS LIMITED", "company_status": "active", "company_number": "1"},
        {"title": "ACME GROUP LIMITED", "company_status": "active", "company_number": "2"},
    ]
    _c, _r, ambiguous = B.resolve_company("Acme", two)
    assert ambiguous is True
