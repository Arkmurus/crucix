"""R-F3053 — a counter-sanction must not produce a mandatory refusal.

LIVE (dd_17ef831d42fe, 2026-07-25). A DD on **Charles Woodburn, the CEO of BAE
Systems**, returned:

    HARD STOP — Charles Woodburn triggers a mandatory refusal.
    Do NOT proceed with the transaction.

The underlying match is CORRECT and was verified against the primary source:
OpenSanctions Q32159211, lists `wikidata, cn_sanctions`, topic `sanction.counter` —
China counter-sanctioned him. But a Chinese counter-designation of a UK defence
executive creates no prohibition for a UK/EU/US counterparty; it is imposed in
retaliation for legitimate Western defence work. For a product whose users ARE
Western defence, HARD_STOPping their own primes' executives is the most damaging
false positive available.

Same correction as R-F569 (`export.risk`, after Embraer HARD_STOPped at an MVP
fire-test) and the same principle as R-F3000 (UK sovereign debt AMBER → INFO):
report the FACT, drop the unjustified BLOCKING VERDICT.
"""
from aria_service.intel._sanctions_classify import classify_matches, _TOPIC_SEVERITY


def _match(topics, name="Charles Woodburn", score=1.0, datasets=("wikidata", "cn_sanctions")):
    return {"name": name, "score": score, "topics": list(topics),
            "datasets": list(datasets), "match_field": "primary_name"}


def test_rf3053_counter_sanction_topic_is_amber_not_hard_stop():
    assert _TOPIC_SEVERITY["sanction.counter"] == "amber"


def test_rf3053_the_live_bae_ceo_case_no_longer_hard_stops():
    """CAPABILITY: the exact live match that produced the false refusal."""
    out = classify_matches([_match(["sanction.counter"])], query_name="Charles Woodburn")
    assert out["worst_severity"] == "amber", (
        "the CEO of a UK prime must not be a mandatory refusal for a UK client")
    assert out["worst_severity"] != "hard_stop"


def test_rf3053_the_finding_explains_what_a_counter_sanction_is():
    """An unexplained AMBER on a named individual reads as an accusation."""
    out = classify_matches([_match(["sanction.counter"])], query_name="Charles Woodburn")
    s = out["summary"]
    assert "COUNTER-SANCTIONS" in s
    assert "NOT a UK/EU/US legal prohibition" in s
    assert "banking, travel" in s, "it is still material — say how"


def test_rf3053_a_real_sanction_still_hard_stops():
    """never-false-clean: this must not become a way to launder a real listing."""
    out = classify_matches([_match(["sanction"], name="Real Target",
                                   datasets=("us_ofac_sdn",))],
                           query_name="Real Target")
    assert out["worst_severity"] == "hard_stop"


def test_rf3053_a_real_sanction_alongside_a_counter_sanction_still_hard_stops():
    """The worst severity wins — a counter-sanction cannot mask an OFAC listing."""
    out = classify_matches(
        [_match(["sanction.counter"]),
         _match(["sanction"], name="Charles Woodburn", datasets=("us_ofac_sdn",))],
        query_name="Charles Woodburn")
    assert out["worst_severity"] == "hard_stop"


def test_rf3053_other_blocking_topics_are_untouched():
    for topic in ("sanction", "sanction.linked", "asset.frozen", "export.control",
                  "icc.wanted", "interpol.red"):
        assert _TOPIC_SEVERITY[topic] == "hard_stop", topic


def test_rf3053_note_is_absent_when_no_counter_sanction_is_involved():
    out = classify_matches([_match(["sanction"], datasets=("us_ofac_sdn",))],
                           query_name="Charles Woodburn")
    assert "COUNTER-SANCTIONS" not in out["summary"]


# ── R-F3054 — the readiness blocker must state the REAL failure ────────────
from aria_service.intel.dd_schema import _dd_decision_readiness


def _identity(**kw):
    base = {"registration_number": "HRB 12345", "registration_status": "active",
            "directors": [], "incorporation_date": None, "data_gaps": []}
    base.update(kw)
    return {"identity": base}


def test_rf3054_live_status_with_no_directors_says_so():
    """LIVE (dd_fd2216746c15, Rheinmetall AG): the blocker read "registry status
    'active' is not a recognised live status" — while 'active' IS the live status the
    gate recognises. The real failure was no directors and no incorporation date."""
    q = _dd_decision_readiness(_identity())["questions"]["identity"]
    assert q["answered"] is False
    assert "not a recognised live status" not in q["blocker"], (
        "'active' is recognised — this was the wrong reason")
    assert "neither directors nor an incorporation date" in q["blocker"]


def test_rf3054_a_genuinely_unrecognised_status_still_says_that():
    q = _dd_decision_readiness(
        _identity(registration_status="cancelled-by-request"))["questions"]["identity"]
    b = q["blocker"]
    assert "cancelled-by-request" in b


def test_rf3054_a_dead_status_is_named_verbatim():
    q = _dd_decision_readiness(
        _identity(registration_status="dissolved"))["questions"]["identity"]
    assert "registry status is 'dissolved'" in q["blocker"]


def test_rf3054_missing_registration_number_is_named():
    q = _dd_decision_readiness(
        _identity(registration_number="", incorporation_date="1889-01-01",
                  directors=[{"name": "X"}]))["questions"]["identity"]
    assert "no registration number" in q["blocker"]


def test_rf3054_registry_unavailable_still_takes_precedence():
    q = _dd_decision_readiness(_identity(
        data_gaps=["R-F1636: registry unavailable — not registry-verified"],
    ))["questions"]["identity"]
    assert "registry was unavailable" in q["blocker"]


def test_rf3054_a_complete_identity_has_no_blocker():
    q = _dd_decision_readiness(_identity(
        directors=[{"name": "A"}], incorporation_date="1889-01-01"))["questions"]["identity"]
    assert q["answered"] is True and q["blocker"] == ""
