"""R-F1676 — the eval judge is GROUNDING-AWARE. When evidence (context) is shown
to the model, the judge prompt must require claim-level support from that evidence
and reject fabricated specifics/citations — the keystone for a trustworthy reward
(it can't be gamed by plausible fabrication that matches the reference). Closed-book
(no context) keeps the original reference-match rubric (back-compat).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "train" / "eval_aria_llm.py"
_spec = importlib.util.spec_from_file_location("eval_aria_llm", _EVAL)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["eval_aria_llm"] = _mod
_spec.loader.exec_module(_mod)


def test_open_book_judge_demands_grounding_and_includes_evidence():
    sys_p, usr_p = _mod._build_judge_prompt(
        question="Is entity X sanctioned?",
        expected="X is on the OFAC SDN list under EO 13224.",
        actual="Yes, X is designated under EO 13224 [from OFAC SDN].",
        context="OFAC SDN: X designated 2023 under EO 13224.",
    )
    # rubric must enforce grounding + anti-fabrication
    assert "GROUNDING" in sys_p
    assert "FABRICATION" in sys_p and "invented citation" in sys_p
    assert "grounded in the evidence" in sys_p
    # the evidence must be shown to the judge (the whole point — it was blind before)
    assert "EVIDENCE" in usr_p and "OFAC SDN: X designated 2023" in usr_p


def test_closed_book_judge_is_unchanged_reference_match():
    sys_p, usr_p = _mod._build_judge_prompt(
        question="q", expected="ref", actual="cand", context="",
    )
    assert "GROUNDING" not in sys_p          # no grounding rubric without evidence
    assert "EVIDENCE" not in usr_p           # no evidence block
    assert "FACTUAL AGREEMENT" in sys_p      # original reference-match rubric intact
    assert "REFERENCE ANSWER" in usr_p
