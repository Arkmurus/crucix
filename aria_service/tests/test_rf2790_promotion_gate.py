"""R-F2790 — the Cycle-6 promotion gate is ENFORCED and FAIL-CLOSED.

Before R-F2790 the promote/no-promote decision was a manual eyeball of the eval
table — exactly where a false "the new checkpoint is better" can slip through,
the opposite of ARIA's never-a-false-clean USP. This pins the machine gate:
promote the sovereign checkpoint ONLY if it clears every honesty+precision floor
AND beats the SAME-RUN DeepSeek baseline on precision AND recall; anything else
(incl. a missing DeepSeek baseline) is NO-PROMOTE, and --report --gate exits 1.
"""
from __future__ import annotations

import json
import types

from scripts.eval.objective_citation_eval import _GATE, _promotion_gate, report


def _agg(precision, recall, mean_fab, zero_fab, grounded_recall=None):
    """A per-side aggregate dict shaped like report()'s agg()."""
    return {
        "citation_precision": precision,
        "mean_fabricated": mean_fab,
        "keyword_recall": recall,
        "grounded_recall": grounded_recall if grounded_recall is not None else recall,
        "mean_reward": 0.5,
        "pct_zero_fabrication": zero_fab,
    }


# A sovereign side that clears every floor, and a DeepSeek baseline it beats.
_SOV_PASS = _agg(0.80, 0.25, 0.05, 0.90)
_DS_BASE = _agg(0.70, 0.22, 0.20, 0.80)


def test_promote_only_when_all_gates_clear():
    promote, lines = _promotion_gate({"sovereign": _SOV_PASS, "deepseek": _DS_BASE})
    assert promote is True
    assert any("PROMOTE" in l and "NO-PROMOTE" not in l for l in lines)


def test_no_promote_when_precision_below_deepseek():
    # Sovereign precision clears the 0.750 floor but LOSES to same-run DeepSeek.
    sov = _agg(0.76, 0.25, 0.05, 0.90)
    ds = _agg(0.78, 0.22, 0.20, 0.80)
    promote, lines = _promotion_gate({"sovereign": sov, "deepseek": ds})
    assert promote is False
    assert any("FAIL" in l and "precision >= DeepSeek" in l for l in lines)


def test_no_promote_when_precision_below_floor():
    sov = _agg(0.74, 0.25, 0.05, 0.90)          # below the 0.750 moat floor
    ds = _agg(0.60, 0.22, 0.20, 0.80)           # even though it beats DeepSeek
    promote, _ = _promotion_gate({"sovereign": sov, "deepseek": ds})
    assert promote is False


def test_no_promote_when_recall_below_deepseek():
    sov = _agg(0.80, 0.24, 0.05, 0.90)
    ds = _agg(0.70, 0.30, 0.20, 0.80)           # DeepSeek recall higher
    promote, _ = _promotion_gate({"sovereign": sov, "deepseek": ds})
    assert promote is False


def test_no_promote_when_fabrication_too_high():
    sov = _agg(0.80, 0.25, 0.19, 0.90)          # mean_fabricated 0.19 > 0.18 ceiling
    promote, _ = _promotion_gate({"sovereign": sov, "deepseek": _DS_BASE})
    assert promote is False


def test_no_promote_when_zero_fab_below_floor():
    sov = _agg(0.80, 0.25, 0.05, 0.84)          # 0.84 < 0.847 floor
    promote, _ = _promotion_gate({"sovereign": sov, "deepseek": _DS_BASE})
    assert promote is False


def test_fail_closed_when_no_deepseek_baseline():
    # Cannot prove ">= DeepSeek" without the same-run baseline -> NO-PROMOTE.
    promote, lines = _promotion_gate({"sovereign": _SOV_PASS})
    assert promote is False
    assert any("fail-closed" in l.lower() for l in lines)


def test_fail_closed_when_no_sovereign_side():
    promote, _ = _promotion_gate({"deepseek": _DS_BASE})
    assert promote is False


def test_thresholds_match_the_documented_gate():
    # The gate the operator signed off on — pin the numbers so a silent edit is caught.
    assert _GATE == {
        "precision_floor": 0.750,
        "recall_floor": 0.238,
        "grounded_recall_floor": 0.40,
        "fabrication_ceiling": 0.18,
        "zero_fab_floor": 0.847,
    }


def test_grounded_gate_uses_grounded_recall_not_raw():
    # R-F2805 — with use_grounded, the recall criteria read grounded_recall. A model
    # with LOW raw recall but HIGH grounded recall that BEATS DeepSeek's grounded recall
    # must PASS the recall criteria (it would FAIL the raw gate).
    sov = _agg(0.80, recall=0.21, mean_fab=0.05, zero_fab=0.90, grounded_recall=0.66)
    ds = _agg(0.70, recall=0.22, mean_fab=0.20, zero_fab=0.80, grounded_recall=0.63)
    # raw gate: recall 0.21 < 0.238 floor AND < DS 0.22 -> would be NO-PROMOTE
    assert _promotion_gate({"sovereign": sov, "deepseek": ds}, use_grounded=False)[0] is False
    # grounded gate: 0.66 >= 0.60 floor AND >= DS 0.63 -> the recall criteria pass; all clear
    promote, lines = _promotion_gate({"sovereign": sov, "deepseek": ds}, use_grounded=True)
    assert promote is True
    assert any("grounded_recall >= DeepSeek" in l and "PASS" in l for l in lines)


def test_grounded_gate_no_promote_when_below_deepseek_grounded():
    sov = _agg(0.80, recall=0.21, mean_fab=0.05, zero_fab=0.90, grounded_recall=0.61)
    ds = _agg(0.70, recall=0.20, mean_fab=0.20, zero_fab=0.80, grounded_recall=0.66)
    promote, lines = _promotion_gate({"sovereign": sov, "deepseek": ds}, use_grounded=True)
    assert promote is False
    assert any("grounded_recall >= DeepSeek" in l and "FAIL" in l for l in lines)


def _write_rows(tmp_path, rows):
    p = tmp_path / "obj_eval.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return str(p)


def _side_row(precision, recall, fabricated):
    """A raw per-row score as objective_citation_eval writes it (agg() re-derives
    mean_fabricated / pct_zero_fabrication from fabricated_citations)."""
    return {"citation_precision": precision, "fabricated_citations": fabricated,
            "keyword_recall": recall, "score": 0.5}


def test_report_gate_exit_zero_on_promote(tmp_path, capsys):
    # End-to-end through report(): a clearly-promotable run must exit 0 under --gate.
    rows = [{"sovereign": _side_row(0.82, 0.26, 0), "deepseek": _side_row(0.70, 0.22, 1)}
            for _ in range(5)]
    args = types.SimpleNamespace(out=_write_rows(tmp_path, rows), gate=True)
    rc = report(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "PROMOTE" in out and "NO-PROMOTE" not in out


def test_report_gate_exit_one_on_no_promote(tmp_path, capsys):
    # Sovereign loses precision to same-run DeepSeek -> report --gate MUST exit 1
    # so an automated cycle cannot promote it.
    rows = [{"sovereign": _side_row(0.70, 0.26, 0), "deepseek": _side_row(0.80, 0.22, 0)}
            for _ in range(5)]
    args = types.SimpleNamespace(out=_write_rows(tmp_path, rows), gate=True)
    rc = report(args)
    out = capsys.readouterr().out
    assert rc == 1
    assert "NO-PROMOTE" in out


def test_report_without_gate_is_unchanged_exit_zero(tmp_path):
    # Backward-compatible: no --gate -> report() returns 0 regardless of the numbers.
    rows = [{"sovereign": _side_row(0.10, 0.10, 5), "deepseek": _side_row(0.90, 0.90, 0)}]
    args = types.SimpleNamespace(out=_write_rows(tmp_path, rows), gate=False)
    assert report(args) == 0
