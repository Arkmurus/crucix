"""R-F3367 — multi-hop traces: every hop's argument must be DERIVED from the last hop's output.

WHY MULTI-HOP IS THE POINT. R-F3366 gave the model a single tool call. That
teaches "call the tool before answering", but not reasoning — one call has no
decision in it. Reasoning is choosing the NEXT tool from what the LAST one
returned. The chain replayed here is the real one the orchestrator performs:

    search_companies("Rolls-Royce Holdings plc")  -> 07524813
    get_officers("07524813")                      -> 51 real officers
    screen("<an officer from that list>")         -> real sanctions result

This is deliberately the officer/PSC -> sanctions path that R-F3353 found had
NEVER executed in production (it called a `screen_entity` attribute that has
never existed). The model is being taught the reasoning the engine performs.

THE INVARIANT THIS FILE EXISTS FOR. `07524813` cannot be known without hop 1.
The officer name cannot be known without hop 2. So every argument of every hop
after the first MUST appear in a previous tool payload. A model that invents a
company number or an officer name between hops is fabricating entities — the
exact north-star violation, and far more dangerous than a wrong answer because
the invented entity then gets screened and reported on with full confidence.

`validate_trace` therefore rejects any tool call whose arguments are not
traceable to prior output (or to the user's own question, for hop 1).
"""
from __future__ import annotations

import json

import pytest

from scripts.train import build_tooluse_corpus as B


# ── real captured payloads (shapes verified against the live registry) ─────

SEARCH = {
    "results": [
        {"company_number": "07524813", "title": "ROLLS-ROYCE HOLDINGS PLC",
         "company_status": "active", "company_type": "plc"},
        {"company_number": "16318460", "title": "AAAC HOLDINGS LIMITED",
         "company_status": "active", "company_type": "ltd"},
    ]
}

OFFICERS = {
    "company_number": "07524813",
    "officers": [
        {"name": "O'GRADY, Claire-Marie", "officer_role": "director", "resigned_on": None},
        {"name": "BEHRENDT, Birgit", "officer_role": "director", "resigned_on": None},
    ],
}

SCREEN_CLEAN = {
    "result": "CLEAR", "status": "CLEAR", "blocked": False,
    "entity": "O'GRADY, Claire-Marie",
    "sanctions": {"matched": False, "matches": [], "verdict": "CLEAR"},
}


def _chain():
    """The real 3-hop chain, as the builder assembles it."""
    return B.build_multihop_trace(
        subject="Rolls-Royce Holdings plc",
        hops=[
            ("companies_house_search", {"query": "Rolls-Royce Holdings plc"}, SEARCH),
            ("companies_house_officers", {"company_number": "07524813"}, OFFICERS),
            ("screen", {"entity_name": "O'GRADY, Claire-Marie"}, SCREEN_CLEAN),
        ],
    )


# ── the trace is genuinely multi-hop ───────────────────────────────────────

def test_chain_has_three_linked_tool_calls():
    t = _chain()
    tool_turns = [m for m in t["messages"] if m["role"] == "tool"]
    assert len(tool_turns) == 3, [m["role"] for m in t["messages"]]
    call_ids = [c["id"] for m in t["messages"] for c in (m.get("tool_calls") or [])]
    assert len(call_ids) == 3 == len(set(call_ids)), call_ids
    assert [m["tool_call_id"] for m in tool_turns] == call_ids


def test_each_hop_reasons_before_calling():
    """A hop with no assistant text is a lookup, not reasoning."""
    t = _chain()
    for m in t["messages"]:
        if m.get("tool_calls"):
            assert (m.get("content") or "").strip(), "tool call emitted with no reasoning"


def test_valid_chain_passes_validation():
    assert B.validate_trace(_chain()) == []


# ── THE DERIVATION INVARIANT ───────────────────────────────────────────────

def test_invented_company_number_is_rejected():
    """A company number that appeared in no prior payload is a fabricated entity."""
    t = B.build_multihop_trace(
        subject="Rolls-Royce Holdings plc",
        hops=[
            ("companies_house_search", {"query": "Rolls-Royce Holdings plc"}, SEARCH),
            ("companies_house_officers", {"company_number": "99999999"}, OFFICERS),
        ],
    )
    errs = B.validate_trace(t)
    assert errs, "a company number no tool returned was accepted"
    assert any("99999999" in e for e in errs), errs


def test_invented_officer_name_is_rejected():
    t = B.build_multihop_trace(
        subject="Rolls-Royce Holdings plc",
        hops=[
            ("companies_house_search", {"query": "Rolls-Royce Holdings plc"}, SEARCH),
            ("companies_house_officers", {"company_number": "07524813"}, OFFICERS),
            ("screen", {"entity_name": "SMITH, Nobody Invented"}, SCREEN_CLEAN),
        ],
    )
    errs = B.validate_trace(t)
    assert errs, "an officer never returned by the registry was screened"
    assert any("Nobody Invented" in e or "entity_name" in e for e in errs), errs


def test_first_hop_may_derive_from_the_user_question():
    """Hop 1 has no prior payload — its argument comes from the user, which is
    legitimate. The guard must not reject the whole chain at its root."""
    t = B.build_multihop_trace(
        subject="Rolls-Royce Holdings plc",
        hops=[("companies_house_search", {"query": "Rolls-Royce Holdings plc"}, SEARCH)],
    )
    assert not [e for e in B.validate_trace(t) if "derive" in e.lower()], B.validate_trace(t)


def test_derivation_is_not_defeated_by_case_or_padding():
    t = B.build_multihop_trace(
        subject="Rolls-Royce Holdings plc",
        hops=[
            ("companies_house_search", {"query": "Rolls-Royce Holdings plc"}, SEARCH),
            ("companies_house_officers", {"company_number": "  07524813 "}, OFFICERS),
            ("screen", {"entity_name": "o'grady, claire-marie"}, SCREEN_CLEAN),
        ],
    )
    assert B.validate_trace(t) == [], B.validate_trace(t)


def test_numeric_and_short_args_do_not_pass_by_accident():
    """A 1-2 char argument would substring-match almost any payload. Such an
    argument cannot be considered derived, or the guard goes blind."""
    t = B.build_multihop_trace(
        subject="X",
        hops=[
            ("companies_house_search", {"query": "X"}, SEARCH),
            ("companies_house_officers", {"company_number": "0"}, OFFICERS),
        ],
    )
    assert B.validate_trace(t), "a 1-character argument was accepted as derived"


# ── the final answer stays grounded across ALL hops ────────────────────────

def test_final_answer_may_cite_any_real_hop_source():
    t = _chain()
    final = t["messages"][-1]["content"]
    assert "07524813" in final or "ROLLS-ROYCE" in final.upper()


def test_citation_from_no_hop_is_still_rejected():
    t = _chain()
    t["messages"][-1]["content"] += " Also listed [from un_sc_consolidated]."
    errs = B.validate_trace(t)
    assert any("un_sc_consolidated" in e for e in errs), errs


def test_clean_officer_screen_is_not_reported_as_a_hit():
    t = _chain()
    t["messages"][-1]["content"] = "The director is sanctioned and must be blocked."
    assert B.validate_trace(t), "a clean officer screen was reported as a hit"


# ── single-hop traces from R-F3366 still validate (no regression) ──────────

def test_single_hop_traces_still_pass():
    single = B.build_trace("Marks and Spencer Group plc", {
        "result": "CLEAR", "status": "CLEAR",
        "sanctions": {"matched": False, "matches": [], "verdict": "CLEAR"},
    })
    assert B.validate_trace(single) == []


def test_multihop_is_written_with_its_own_label(tmp_path):
    out = tmp_path / "c.jsonl"
    n = B.write_multihop_corpus([_chain()], out, eval_subjects=set())
    assert n == 1
    row = json.loads(out.read_text(encoding="utf-8").strip())
    assert row["label"] == "tooluse_multihop"
    assert row["hops"] == 3


def test_contaminated_multihop_subject_is_dropped(tmp_path):
    out = tmp_path / "c.jsonl"
    n = B.write_multihop_corpus([_chain()], out, eval_subjects={"rolls-royce holdings"})
    assert n == 0, "an eval-set subject was written as a multi-hop trace"
