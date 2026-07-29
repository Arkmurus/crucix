"""R-F3407 — "no sanctions match" plus adverse coverage is NOT a clean verdict.

This is the USP as training data. The single most damaging thing ARIA can say is
"clean" about an entity that isn't, and the shape that produces it is not an
exotic bug — it is the ordinary case where one tool returns nothing and another
returns something. A screen answers "is this entity on the lists I checked".
It does not answer "is this entity a problem". A model that has only ever seen
screen -> no match -> "no concerns" learns to collapse the two.

So the corpus needs traces where the tools DISAGREE and the honest answer names
the disagreement instead of resolving it silently in the reassuring direction.

The validator enforces what is checkable and is exactly where fabrication
enters:
  * with adverse coverage present, the answer may NOT assert a clean verdict
  * it must cite only outlets the search actually returned
  * it must say what the screen did and did not cover

NEGATION IS THE TRAP. "This is NOT a clean result" contains the word "clean" and
is the CORRECT answer. A naive keyword check flags it and would train the model
away from the very phrasing wanted. That mistake has been made repeatedly in
this repo, so it is pinned by test here.
"""
from __future__ import annotations

import pytest

from scripts.train import build_tooluse_corpus as B


CLEAN_SCREEN = {"status": "OK", "result": "NO_MATCH", "entity": "Acme Holdings",
                "sanctions": {"screened": True, "matches": [],
                              "sources": ["OFAC SDN", "UK OFSI", "EU CFSP"]}}

ADVERSE_SEARCH = {"results": [
    {"title": "Acme Holdings faces bribery probe", "url": "https://www.reuters.com/x",
     "snippet": "Prosecutors opened an investigation into Acme Holdings."},
    {"title": "Regulator fines Acme unit", "url": "https://www.ft.com/y",
     "snippet": "The regulator imposed a penalty on an Acme subsidiary."},
]}

NO_ADVERSE = {"results": []}


def _final(trace: dict) -> str:
    return trace["messages"][-1]["content"]


# --------------------------------------------------------------------------
# the trace itself
# --------------------------------------------------------------------------

def test_trace_is_valid_and_labelled():
    t = B.build_contradiction_trace("Acme Holdings", CLEAN_SCREEN, ADVERSE_SEARCH)
    assert B.validate_trace(t) == []
    assert t["label"] == "tooluse_contradiction"
    assert t["subject"] == "Acme Holdings"


def test_trace_calls_both_tools_before_answering():
    t = B.build_contradiction_trace("Acme Holdings", CLEAN_SCREEN, ADVERSE_SEARCH)
    called = [tc["function"]["name"]
              for m in t["messages"] for tc in (m.get("tool_calls") or [])]
    assert len(called) >= 2, "one tool cannot produce a disagreement"
    assert len(set(called)) >= 2, "the two calls must be different tools"


def test_answer_refuses_to_call_it_clean_when_adverse_coverage_exists():
    t = B.build_contradiction_trace("Acme Holdings", CLEAN_SCREEN, ADVERSE_SEARCH)
    final = _final(t).lower()
    assert "not a clean" in final or "does not mean" in final or "not clear" in final


def test_answer_states_what_the_screen_did_and_did_not_cover():
    """'No match' is meaningless without the list of lists."""
    t = B.build_contradiction_trace("Acme Holdings", CLEAN_SCREEN, ADVERSE_SEARCH)
    final = _final(t)
    assert "OFAC SDN" in final and "UK OFSI" in final


def test_answer_cites_only_outlets_the_search_returned():
    t = B.build_contradiction_trace("Acme Holdings", CLEAN_SCREEN, ADVERSE_SEARCH)
    final = _final(t)
    assert "reuters.com" in final or "Reuters" in final
    assert "bloomberg" not in final.lower(), "an outlet the search never returned"


def test_no_adverse_coverage_produces_no_contradiction_trace():
    """Without a disagreement there is nothing for this axis to teach."""
    assert B.build_contradiction_trace("Acme Holdings", CLEAN_SCREEN, NO_ADVERSE) is None


def test_a_screen_that_did_not_run_is_not_a_contradiction():
    """An unavailable source is the R-F3396 axis, not this one."""
    broken = {"status": "ERROR", "result": "UNKNOWN", "entity": "Acme Holdings",
              "sanctions": {"screened": False, "error": "source unreachable"}}
    assert B.build_contradiction_trace("Acme Holdings", broken, ADVERSE_SEARCH) is None


# --------------------------------------------------------------------------
# the validator — the part that must not be fooled
# --------------------------------------------------------------------------

def test_validator_rejects_a_clean_verdict_alongside_adverse_coverage():
    t = B.build_contradiction_trace("Acme Holdings", CLEAN_SCREEN, ADVERSE_SEARCH)
    t["messages"][-1]["content"] = (
        "Acme Holdings returned no sanctions matches. The entity is clean and "
        "no further action is required."
    )
    errs = B.validate_trace(t)
    assert errs, "a false clean must never survive validation"
    assert any("clean" in e.lower() for e in errs)


@pytest.mark.parametrize("phrasing", [
    "This is NOT a clean result: adverse reporting exists (reuters.com).",
    "A no-match screen does not mean the entity is clean — see reuters.com.",
    "I cannot call this clean. Reuters reports a bribery probe.",
    "Treating this as clean would be wrong; reuters.com reports an investigation.",
])
def test_validator_allows_negated_mentions_of_clean(phrasing):
    """The correct answer necessarily contains the word it is refusing.

    A naive keyword check flags every one of these and would train the model
    away from exactly the phrasing wanted.
    """
    t = B.build_contradiction_trace("Acme Holdings", CLEAN_SCREEN, ADVERSE_SEARCH)
    t["messages"][-1]["content"] = phrasing + " Screened against OFAC SDN, UK OFSI, EU CFSP."
    assert B.validate_trace(t) == [], f"false positive on: {phrasing!r}"


def test_validator_rejects_an_outlet_the_search_never_returned():
    """Whether cited in brackets or smuggled in as prose."""
    for text in (
        "This is not a clean result. See [from bloomberg.com] for the probe.",
        "This is not a clean result. bloomberg.com reports a bribery probe.",
    ):
        t = B.build_contradiction_trace("Acme Holdings", CLEAN_SCREEN, ADVERSE_SEARCH)
        t["messages"][-1]["content"] = text + " Screened against OFAC SDN, UK OFSI, EU CFSP."
        assert B.validate_trace(t), f"fabricated outlet survived: {text!r}"


def test_a_bare_BRAND_name_is_a_known_gap_not_a_covered_case():
    """Honest about what is NOT checked, so nobody reads coverage that isn't there.

    `_independent_sources` returns DOMAINS. Catching the prose word "Bloomberg"
    would need a brand->domain table, and inventing one here would be a guess
    list that cries wolf on legitimate prose ("unlike Reuters, the filing...").
    The captured corpus is machine-built and always cites domains, so the gap is
    not reachable by the generator - it would only matter for hand-written rows.
    """
    t = B.build_contradiction_trace("Acme Holdings", CLEAN_SCREEN, ADVERSE_SEARCH)
    t["messages"][-1]["content"] = (
        "This is not a clean result. Bloomberg reports a probe. "
        "Screened against OFAC SDN, UK OFSI, EU CFSP."
    )
    assert B.validate_trace(t) == [], "documenting current behaviour, not endorsing it"


def test_memory_only_coverage_is_not_a_contradiction():
    """ARIA's own memory disagreeing with a screen is one source talking to itself.

    A live search returns `memory://` entries beside real coverage.
    `_independent_sources` excludes them, and the first live capture proved why:
    the builder quoted those memory hits as outlets and the validator refused
    every trace. Outside evidence, or no trace.
    """
    memory_only = {"results": [
        {"title": "prior note on Acme", "url": "memory://aria/notes/1", "snippet": "x"},
        {"title": "another note", "url": "memory://aria/notes/2", "snippet": "y"},
    ]}
    assert B.build_contradiction_trace("Acme Holdings", CLEAN_SCREEN, memory_only) is None


def test_memory_hits_are_dropped_but_real_outlets_still_build():
    mixed = {"results": [
        {"title": "prior note", "url": "memory://aria/notes/1", "snippet": "x"},
        {"title": "Acme faces probe", "url": "https://www.reuters.com/x", "snippet": "y"},
    ]}
    t = B.build_contradiction_trace("Acme Holdings", CLEAN_SCREEN, mixed)
    assert t is not None and B.validate_trace(t) == []
    assert "memory:" not in _final(t)
    assert "reuters.com" in _final(t)


def test_every_cited_outlet_is_present_in_the_recorded_tool_payload():
    """The payload was truncated to 5 while the answer used the full list.

    Emirates NBD returned 4 memory hits + apnews + reuters; `results[:5]` cut
    reuters out of the tool turn while the answer still cited it, so the trace
    cited an outlet its own recorded evidence did not contain. A tool turn must
    be what the tool returned.
    """
    many = {"results": (
        [{"title": f"note {i}", "url": f"memory://n{i}", "snippet": "x"} for i in range(4)]
        + [{"title": "AP probe piece", "url": "https://apnews.com/a", "snippet": "y"},
           {"title": "Reuters fine piece", "url": "https://www.reuters.com/b", "snippet": "z"}]
    )}
    t = B.build_contradiction_trace("Acme Holdings", CLEAN_SCREEN, many)
    assert t is not None
    assert B.validate_trace(t) == []
    payload = " ".join(m.get("content", "") for m in t["messages"] if m.get("role") == "tool")
    for cited in B._CITE_RE.findall(_final(t)):
        assert cited in payload, f"cited {cited!r} is absent from the tool payload"
