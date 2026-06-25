"""R-F1943 — DPO preference pairs must be VERIFIED by the grounding reward
(chosen out-scores rejected by a margin), not taken on faith. Tests the pure
helpers + the verification principle the builder enforces."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from aria_service.intel import grounding_reward as gr

_BUILDER = Path(__file__).resolve().parents[2] / "scripts" / "admin" / "build_preference_pairs.py"


def _mod():
    spec = importlib.util.spec_from_file_location("pref_builder", _BUILDER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_question_extraction():
    m = _mod()
    user = "[CONTEXT — ...]\n- fact [Source: a]\n[QUESTION]\nWhat is X?"
    assert m._question_of(user) == "What is X?"
    assert m._question_of("no marker just text").strip() == "no marker just text"


def test_preference_is_objectively_verified():
    """The builder keeps a pair only when reward(chosen) - reward(rejected) >=
    margin. A grounded chosen vs an ungrounded/fabricated rejected must clear it."""
    m = _mod()
    ctx = "[RAG RETRIEVED]\n- The UK uses SAMLA 2018. [Source: intlaw:samla]\n"
    chosen = "The UK implements sanctions under SAMLA 2018 [Source: intlaw:samla]."
    rejected = "The UK uses the 1998 Sanctions Act [Source: parliament:act_1998]."  # fabricated source
    rc = gr.reward(chosen, ctx)
    rr = gr.reward(rejected, ctx)
    assert rc - rr >= m._MARGIN          # this pair WOULD be kept
    assert rc > rr


def test_margin_threshold_filters_weak_pairs():
    """Two equally (un)grounded answers must NOT clear the margin -> dropped."""
    m = _mod()
    ctx = "[RAG RETRIEVED]\n- fact [Source: real:src]\n"
    a = "The answer is X [Source: real:src]."
    b = "The answer is Y [Source: real:src]."
    assert abs(gr.reward(a, ctx) - gr.reward(b, ctx)) < m._MARGIN  # below margin -> drop
