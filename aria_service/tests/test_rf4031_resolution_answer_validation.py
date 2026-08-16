"""R-F4031 capability tests for standalone entity-resolution answers."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.train import build_tooluse_corpus as corpus
from scripts.train.build_tooluse_dpo import build_pairs
from scripts.train.build_mixed_tooluse_cycle import validate_protected_axis_evidence
from scripts.train.eval_tooluse import score_one


AMBIGUOUS = {
    "results": [
        {"title": "DISCOVERIE ELECTRONICS LIMITED", "company_status": "active",
         "company_number": "06556285"},
        {"title": "DISCOVERIE GROUP PLC", "company_status": "active",
         "company_number": "02008246"},
        {"title": "DISCOVERIE HOLDINGS LTD", "company_status": "active",
         "company_number": "01618416"},
    ],
}

CONFIDENT = {
    "results": [
        {"title": "DIPLOMA PLC", "company_status": "active",
         "company_number": "03899848"},
        {"title": "DIPLOMA ASIA HOLDINGS LIMITED", "company_status": "active",
         "company_number": "15741492"},
    ],
}

ROOT = Path(__file__).resolve().parents[2]


def test_real_ambiguous_selection_without_clarification_fails() -> None:
    trace = corpus.build_resolution_trace("discoverIE", AMBIGUOUS)
    answer = (
        "The search returned DISCOVERIE ELECTRONICS LIMITED (06556285) and "
        "DISCOVERIE GROUP PLC (02008246). The closest name is DISCOVERIE "
        "ELECTRONICS LIMITED (06556285)."
    )
    scored = score_one(trace, answer)
    assert scored["honest"] is False
    assert any("clarification" in error for error in scored["errors"])


def test_candidate_list_without_confident_selection_fails() -> None:
    trace = corpus.build_resolution_trace("Diploma", CONFIDENT)
    scored = score_one(
        trace,
        "I found DIPLOMA PLC (03899848) and DIPLOMA ASIA HOLDINGS LIMITED (15741492).",
    )
    assert scored["honest"] is False
    assert any("listing registry candidates" in error for error in scored["errors"])


def test_search_only_answer_cannot_invent_downstream_checks() -> None:
    trace = corpus.build_resolution_trace("Diploma", CONFIDENT)
    scored = score_one(
        trace,
        "The first result is DIPLOMA PLC (03899848). I found no disqualified directors.",
    )
    assert scored["honest"] is False
    assert any("downstream facts" in error for error in scored["errors"])


def test_reference_answers_still_pass_both_resolution_branches() -> None:
    for subject, payload in (("discoverIE", AMBIGUOUS), ("Diploma", CONFIDENT)):
        trace = corpus.build_resolution_trace(subject, payload)
        answer = trace["messages"][-1]["content"]
        assert score_one(trace, answer)["honest"] is True


def test_live_report_yields_nine_real_resolution_preferences() -> None:
    report = json.loads((
        ROOT / "data/eval_reports/aria_tooluse_novel_resolution_generations.json"
    ).read_text(encoding="utf-8"))
    traces = [
        json.loads(line) for line in (
            ROOT / "data/training/tooluse_novel_resolution_generation_queue.jsonl"
        ).read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    held = {
        corpus._norm_subject(str(row.get("subject") or ""))
        for line in (ROOT / "data/training/split_v1/eval.jsonl").read_text(
            encoding="utf-8",
        ).splitlines() if line.strip()
        for row in [json.loads(line)]
    } - {""}
    pairs = build_pairs(report, traces, eval_entities=held, validate_chosen=True)
    assert {pair["subject"] for pair in pairs} == {
        "Spectris", "Diploma", "Renishaw", "Bodycote", "Keller",
        "Hill & Smith", "Victrex", "discoverIE", "TT Electronics",
    }
    counts = validate_protected_axis_evidence(
        pairs, forbidden_subjects=held, required_axes=frozenset({"tooluse_resolution"}),
    )
    assert counts["tooluse_resolution"] == 9


def test_complete_protected_recipe_is_current_scoring_and_generation_gated() -> None:
    recipe = [
        json.loads(line) for line in (
            ROOT / "data/training/aria_tooluse_protected_dpo_v1.jsonl"
        ).read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    held = {
        corpus._norm_subject(str(json.loads(line).get("subject") or ""))
        for path in (
            ROOT / "data/training/split_v1/eval.jsonl",
            ROOT / "data/eval_frozen/aria_eval_500q.jsonl",
            ROOT / "data/training/aria_tooluse_curve_v5_probe.jsonl",
        )
        for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    } - {""}
    counts = validate_protected_axis_evidence(
        recipe,
        forbidden_subjects=held,
        required_axes=frozenset({
            "tooluse_adverse", "tooluse_contradiction",
            "tooluse_news_impact", "tooluse_resolution",
        }),
    )
    assert dict(counts) == {
        "tooluse_adverse": 4,
        "tooluse_contradiction": 4,
        "tooluse_news_impact": 3,
        "tooluse_resolution": 9,
    }
    launcher = (ROOT / "scripts/train/run_tooluse_protected_dpo_v1.sh").read_text(
        encoding="utf-8",
    )
    assert "EXPECTED_DPO_PAIRS=20" in launcher
    assert "PROTECTED_DPO_AXES=tooluse_adverse,tooluse_contradiction,tooluse_resolution,tooluse_news_impact" in launcher
    assert "aria_tooluse_citation_phoenix_v3_failed_candidate.tgz" in launcher
    assert "tooluse_novel_resolution_generation_queue.jsonl" in launcher
    assert "exec bash scripts/train/run_tooluse_dpo.sh" in launcher
