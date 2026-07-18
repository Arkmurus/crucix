"""R-F2751 — ACLED must not attribute political-violence events to a same-substring actor.

`acled.lookup` queries with `actor1_where=LIKE` (a substring match) and appended EVERY
matched event to result["hits"] with no name-confirmation. So a subject substring-matching
an unrelated armed actor got that actor's events (a SEVERE RED finding) attributed to it.
R-F2751 adds the R-F2747-style token gate: an actor-query hit survives only if the subject
is genuinely a PARTY; a country-tempo event is kept as operational-environment context.
(The path is dormant without ACLED creds; this closes the class so it is safe when set.)
"""
from __future__ import annotations

import asyncio

import aria_service.intel.sources.acled as a


def _patch_fetch(monkeypatch, by_call):
    """by_call: list of event-lists returned by successive _fetch calls, in task order
    (actor1, actor2, [country])."""
    calls = {"i": 0}

    async def _fetch(params, timeout=20.0):
        i = calls["i"]
        calls["i"] += 1
        return by_call[i] if i < len(by_call) else []

    monkeypatch.setattr(a, "_fetch", _fetch)
    monkeypatch.setattr(a, "_creds", lambda: ("e@x", "pw"))  # bypass the dormant guard


def _ev(actor1, **kw):
    d = {"data_id": kw.get("data_id", actor1), "actor1": actor1, "actor2": kw.get("actor2", ""),
         "event_date": kw.get("event_date", "2026-07-01"), "event_type": "Violence",
         "country": kw.get("country", "Nowhere"), "fatalities": kw.get("fatalities", 1)}
    return d


def test_rf2751_substring_actor_false_match_dropped(monkeypatch):
    # actor1 LIKE query returns an UNRELATED actor that only substring-matches "Aero".
    _patch_fetch(monkeypatch, [
        [_ev("Aeroflot Russian Airlines", data_id="1")],  # actor1 — false substring match
        [],                                                # actor2
    ])
    res = asyncio.run(a.lookup("Aero"))
    assert res.get("hits") == [], "a substring-only actor must NOT be attributed to the subject"
    assert res.get("off_subject_dropped") == 1
    assert res.get("severity_hint", "").startswith("RED") is False, "no false RED"


def test_rf2751_genuine_party_kept_and_flagged_red(monkeypatch):
    _patch_fetch(monkeypatch, [
        [_ev("Wagner Group", data_id="2", fatalities=12)],  # actor1 — genuine party
        [],
    ])
    res = asyncio.run(a.lookup("Wagner Group"))
    hits = res.get("hits") or []
    assert len(hits) == 1 and hits[0]["subject_is_party"] is True
    assert res.get("severity_hint", "").startswith("RED")


def test_rf2751_country_tempo_context_preserved_not_attributed(monkeypatch):
    # name + country: the country-tempo events are NOT the subject as a party, but must
    # be kept as operational-environment context (context_only), never dropped.
    _patch_fetch(monkeypatch, [
        [],                                                      # actor1 — no genuine match
        [],                                                      # actor2
        [_ev("Some Local Militia", data_id="9", country="Mali")],  # country tempo
    ])
    res = asyncio.run(a.lookup("Acme Defense Ltd", country="Mali"))
    hits = res.get("hits") or []
    assert len(hits) == 1, "country-tempo context must survive"
    assert hits[0]["context_only"] is True and hits[0]["subject_is_party"] is False
    assert res.get("severity_hint", "").startswith("INFO"), "context → INFO, never RED"
