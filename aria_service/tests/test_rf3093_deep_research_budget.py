"""R-F3093 — a DEEP DD that read nothing.

LIVE DEFECT (Mitie, operator report 2026-07-26). The report's own data gap said:

    "deep research was bounded at 37s and stopped after article read (no budget
     left to analyse articles) — 0 article(s) analysed, 0 fact(s) retained"

and the adverse-media screen reported "Query templates actually searched: 12 of 48".
Mode was `deep`. With no evidence gathered, the renderer had nothing to lay out and
filled the page with process narration — which is the actual reason the report reads
as noise. Layout was the symptom; an empty evidence set was the cause.

WHY 37s. The budget is a three-level nest, all three sized for a STANDARD run:

    total 660s  →  digital layer 180s  →  deep-research op 40s (−3s margin)

`_bounded_dd_op` clamps every op to the LAYER deadline (R-F3059), and the digital
layer had already spent ~82s on multi-query + search before deep research started.
So raising the op timeout ALONE changes nothing — these tests pin all three levels
together, and pin that standard/quick are untouched (the 660s total is what
guarantees WhatsApp async-push delivery inside the 15-min poll window).
"""
import inspect

import pytest

from aria_service.intel import dd_orchestrator as ddo

# R-F3783/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source


@pytest.fixture
def clean_budget_env(monkeypatch):
    """These assertions are about DEFAULTS — an inherited env var would mask them."""
    monkeypatch.delenv("ARIA_DD_TOTAL_BUDGET_S", raising=False)
    monkeypatch.delenv("ARIA_DD_DEEP_TOTAL_BUDGET_S", raising=False)
    return monkeypatch


# ── the three levels move together, for deep mode only ─────────────────────
def test_rf3093_deep_mode_total_budget_is_raised(clean_budget_env):
    assert ddo._dd_total_budget_default("deep") >= 1000.0
    assert ddo._dd_total_budget_default("deep") > ddo._dd_total_budget_default("standard")


def test_rf3093_standard_and_quick_keep_the_whatsapp_safe_budget(clean_budget_env):
    """Raising these would push a standard DD past the 15-min WA poll window."""
    assert ddo._dd_total_budget_default("standard") == 660.0
    assert ddo._dd_total_budget_default("quick") == 660.0
    assert ddo._dd_total_budget_default("") == 660.0
    assert ddo._dd_total_budget_default(None) == 660.0


def test_rf3093_deep_mode_total_is_env_tunable(clean_budget_env):
    clean_budget_env.setenv("ARIA_DD_DEEP_TOTAL_BUDGET_S", "900")
    assert ddo._dd_total_budget_default("deep") == 900.0


def test_rf3093_explicit_global_budget_still_wins_in_every_mode(clean_budget_env):
    """R-F1572's ARIA_DD_TOTAL_BUDGET_S is the documented handle for bounding an
    overrunning DD (and the hard-deadline test drives a deep run through it). A
    mode-specific default that ignored it would make deep runs unbounded by the very
    knob the runbook says to reach for."""
    clean_budget_env.setenv("ARIA_DD_TOTAL_BUDGET_S", "1")
    assert ddo._dd_total_budget_default("deep") == 1.0
    assert ddo._dd_total_budget_default("standard") == 1.0


def test_rf3093_deep_research_op_budget_is_no_longer_37s():
    assert ddo._OP_T_DEEPRESEARCH_DEEP >= 240.0, (
        "R-F3093 REGRESSION: deep research is back on a budget too short to read")
    assert ddo._OP_T_DEEPRESEARCH == 40.0, "standard/quick unchanged"


def test_rf3093_digital_layer_can_actually_hold_the_deep_research_op():
    """The op budget is meaningless if the LAYER cannot afford it — that is exactly
    why the 40s bound behaved like 37s and then like nothing."""
    other_ops = (ddo._OP_T_MULTIQUERY + ddo._OP_T_WEBSEARCH + ddo._OP_T_WEBSITE_MINE
                 + ddo._OP_T_RAG + ddo._OP_T_KB)
    assert ddo._DEEP_DIGITAL_BUDGET_S >= ddo._OP_T_DEEPRESEARCH_DEEP + other_ops, (
        "deep research would be starved by the ops that run before it")


def test_rf3093_total_budget_covers_the_deep_digital_layer():
    assert ddo._dd_total_budget_default("deep") > ddo._DEEP_DIGITAL_BUDGET_S + 200


# ── the wiring, at the call sites that were broken ─────────────────────────
def test_rf3093_digital_layer_budget_is_mode_aware():
    src = module_source(ddo)
    assert '_DEEP_DIGITAL_BUDGET_S if mode == "deep"' in src, (
        "R-F3093 REGRESSION: the digital layer is back on the flat standard budget, "
        "so the raised op timeout is clamped away again")


def test_rf3093_deep_research_op_selects_the_deep_budget():
    src = function_source(ddo, "_run_digital")
    assert "_OP_T_DEEPRESEARCH_DEEP if _mode_is_deep else _OP_T_DEEPRESEARCH" in src
    assert "_bounded_dd_op(deep_researcher.investigate" in src
    assert "_dr_op_budget, report.digital" in src, (
        "the bounded op must receive the mode-selected budget, not the flat one")


def test_rf3093_deep_mode_runs_thorough_research():
    src = function_source(ddo, "_run_digital")
    assert 'dr_depth = "thorough" if _mode_is_deep else "quick"' in src


# ── adverse media: a screen that covers a quarter of its templates ─────────
def test_rf3093_adverse_followup_budget_covers_more_templates():
    src = function_source(ddo, "_run_adverse_media_followup")
    assert '_env_int("ARIA_DD_ADVERSE_FOLLOWUP_S", 420)' in src, (
        "R-F3093 REGRESSION: 180s covered 12 of 48 query templates on the live run")


def test_rf3093_adverse_followup_is_still_detached_from_the_dd_budget():
    """The longer sweep must NOT be paid for out of the DD's wall-clock — it merges
    after the verdict is delivered."""
    src = module_source(ddo)
    assert "adverse-media deep search is now DECOUPLED from the 660s budget" in src
