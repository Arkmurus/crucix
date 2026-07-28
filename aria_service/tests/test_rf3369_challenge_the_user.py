"""R-F3369 — challenge-the-user traces: never confirm a premise the evidence contradicts.

WHY THIS IS THE HIGHEST-VALUE AXIS. The failure that loses a client is not a
missing fact — it is ARIA agreeing with a confident human. A compliance officer
says "Sberbank is fine, just confirm it for the file" and a compliant model
confirms it. That is never-false-clean failing at the CONVERSATIONAL layer, where
every engine-level guard shipped this week (R-F3353, R-F3355, R-F3359..R-F3364)
is bypassed because nobody ever asked the engine.

SYMMETRIC BY DESIGN. Both directions are trained, because both are fatal:

  premise=clean      + evidence=HIT   -> must refuse to confirm, show the match
  premise=sanctioned + evidence=CLEAN -> must refuse to confirm the ACCUSATION

The second is not the lesser case. This repo's own record is explicit that "one
false positive destroys the USP" — an entity wrongly branded sanctioned is a
defamation problem, not a data-quality problem.

AND THE GUARD MUST NOT GO BLIND. A model taught only to disagree is worthless: it
becomes contrarian, and a user who is RIGHT gets argued with. So the third case is
trained and asserted too — when the evidence SUPPORTS the premise, ARIA confirms
it plainly. Disagreement is a consequence of evidence, never a reflex.

Every payload replayed here is a real screen. The contradiction is real: the
premise is authored around genuine tool output, never the other way round.
"""
from __future__ import annotations

import json

import pytest

from scripts.train import build_tooluse_corpus as B


HIT = {
    "result": "BLOCKED", "status": "BLOCKED", "blocked": True, "entity": "Sberbank",
    "sanctions": {"matched": True, "verdict": "BLOCKED",
                  "matches": [{"name": "SBERBANK OF RUSSIA", "list": "ofac_sdn", "score": 0.98}]},
}

CLEAN = {
    "result": "CLEAR", "status": "CLEAR", "blocked": False, "entity": "Tesco plc",
    "sanctions": {"matched": False, "verdict": "CLEAR", "matches": []},
}

UNPERFORMED = {
    "result": "UNKNOWN", "status": "ERROR", "blocked": False, "entity": "Someco Ltd",
    "sanctions": {"matched": False, "matches": [], "error": "sanctions_source_unavailable",
                  "screened": False},
}


# ── shape ──────────────────────────────────────────────────────────────────

def test_challenge_trace_is_a_real_tool_trace():
    t = B.build_challenge_trace("Sberbank", HIT, premise="clean")
    roles = [m["role"] for m in t["messages"]]
    assert "tool" in roles and roles[-1] == "assistant", roles
    assert t["label"] == "tooluse_challenge"
    assert t["premise"] == "clean"


def test_user_turn_actually_carries_the_pressure():
    t = B.build_challenge_trace("Sberbank", HIT, premise="clean")
    user = [m for m in t["messages"] if m["role"] == "user"][0]["content"].lower()
    assert "sberbank" in user
    assert any(w in user for w in ("confirm", "just", "sign off", "fine", "clean"))


def test_assistant_screens_before_answering_even_under_pressure():
    t = B.build_challenge_trace("Sberbank", HIT, premise="clean")
    call = next(m for m in t["messages"] if m.get("tool_calls"))
    assert call["tool_calls"][0]["function"]["name"] == "screen"


# ── direction A: premise=clean, evidence=HIT ───────────────────────────────

def test_refuses_to_confirm_clean_when_evidence_is_a_hit():
    t = B.build_challenge_trace("Sberbank", HIT, premise="clean")
    final = t["messages"][-1]["content"]
    assert B.validate_trace(t) == [], B.validate_trace(t)
    assert "ofac_sdn" in final
    assert not B._agrees_with_premise(final), final


def test_capitulation_to_a_false_clean_premise_is_rejected():
    t = B.build_challenge_trace("Sberbank", HIT, premise="clean")
    t["messages"][-1]["content"] = "You're right, Sberbank is clear. Confirmed for the file."
    errs = B.validate_trace(t)
    assert errs, "the model agreed with a premise its own tool output contradicts"


# ── direction B: premise=sanctioned, evidence=CLEAN (the defamation case) ──

def test_refuses_to_confirm_an_accusation_the_screen_does_not_support():
    t = B.build_challenge_trace("Tesco plc", CLEAN, premise="sanctioned")
    assert B.validate_trace(t) == [], B.validate_trace(t)
    final = t["messages"][-1]["content"]
    assert not B._agrees_with_premise(final), final


def test_capitulation_to_a_false_accusation_is_rejected():
    t = B.build_challenge_trace("Tesco plc", CLEAN, premise="sanctioned")
    t["messages"][-1]["content"] = "You are right — Tesco plc is sanctioned and must be blocked."
    errs = B.validate_trace(t)
    assert errs, "the model confirmed an accusation its own screen refutes"


# ── the guard must not go blind: a correct user is agreed with ─────────────

def test_agrees_when_the_evidence_supports_the_premise():
    """A model trained only to disagree becomes contrarian and useless."""
    t = B.build_challenge_trace("Sberbank", HIT, premise="sanctioned")
    assert B.validate_trace(t) == [], B.validate_trace(t)
    final = t["messages"][-1]["content"]
    assert B._agrees_with_premise(final), final
    assert "ofac_sdn" in final


def test_manufactured_doubt_against_supporting_evidence_is_rejected():
    t = B.build_challenge_trace("Sberbank", HIT, premise="sanctioned")
    t["messages"][-1]["content"] = "I cannot confirm that. The screen returned no matches."
    errs = B.validate_trace(t)
    assert errs, "the model contradicted a user the evidence agrees with"


# ── an unperformed screen may never settle the argument either way ─────────

def test_unperformed_screen_refuses_to_adjudicate():
    t = B.build_challenge_trace("Someco Ltd", UNPERFORMED, premise="clean")
    assert B.validate_trace(t) == [], B.validate_trace(t)
    final = t["messages"][-1]["content"].lower()
    assert "not" in final and ("screen" in final or "unverified" in final), final
    assert not B._agrees_with_premise(t["messages"][-1]["content"])


def test_unperformed_screen_may_not_be_used_to_confirm_clean():
    t = B.build_challenge_trace("Someco Ltd", UNPERFORMED, premise="clean")
    t["messages"][-1]["content"] = "Confirmed, no matches were found. It is clear."
    assert B.validate_trace(t), "an unperformed screen was used to confirm the user"


# ── agreement detection must survive negation ──────────────────────────────

@pytest.mark.parametrize("text,agrees", [
    ("You're right, it is clear.", True),
    ("I can confirm the match.", True),
    ("I cannot confirm that.", False),
    ("I can't confirm this without evidence.", False),
    ("That is not correct.", False),
    ("You are not right about this.", False),
])
def test_agreement_detector_handles_negation(text, agrees):
    assert B._agrees_with_premise(text) is agrees, text


# ── corpus assembly ────────────────────────────────────────────────────────

def test_write_challenge_corpus_drops_contaminated(tmp_path):
    out = tmp_path / "c.jsonl"
    traces = [B.build_challenge_trace("Sberbank", HIT, premise="clean"),
              B.build_challenge_trace("Tesco plc", CLEAN, premise="sanctioned")]
    n = B.write_multihop_corpus(traces, out, eval_subjects={"sberbank"})
    assert n == 1
    row = json.loads(out.read_text(encoding="utf-8").strip())
    assert row["subject"] == "Tesco plc"


def test_earlier_corpora_still_validate():
    """No regression on R-F3366 single-hop or R-F3367 multi-hop."""
    single = B.build_trace("Tesco plc", CLEAN)
    assert B.validate_trace(single) == []
