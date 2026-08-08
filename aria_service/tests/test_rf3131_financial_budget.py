"""R-F3131 — a capability shipped into a budget it could never complete in.

MEASURED on the live Babcock DD (dd_8c7242c2b45b, 2026-07-26):

    "financial health did not complete within 25s (bounded) — partial result"
    "Financial capacity — UNRESOLVED — financial capacity is unknown"

R-F3124/R-F3128 added the issuer-annual-report route to exactly that op. It must
fetch a multi-hundred-page PDF, extract its text layer, and send ~120k characters to
Claude. That cannot happen in 25 SECONDS — so financial capacity stayed UNKNOWN for
the one counterparty class the route exists to answer: a listed group.

This is R-F3093 repeated. There, deep research had 37s and read ZERO articles. Same
lesson and the same three-level fix, because `_bounded_dd_op` clamps every op to the
LAYER deadline (R-F3059) — raising the op alone changes nothing:

    total budget  ->  compliance LAYER  ->  financial OP
"""
import inspect

from aria_service.intel import dd_orchestrator as ddo

# R-F3783/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source


def test_rf3131_deep_financial_op_can_actually_read_a_report():
    assert ddo._OP_T_FINANCIAL_DEEP >= 120.0, (
        "R-F3131 REGRESSION: the issuer-report route is back on a budget too short "
        "to fetch a PDF and call a model")
    assert ddo._OP_T_FINANCIAL == 25.0, "standard/quick unchanged"


def test_rf3131_the_layer_can_afford_the_op():
    """The op budget is meaningless if the LAYER cannot hold it — that is exactly why
    25s behaved like a hard stop."""
    others = ddo._OP_T_USASPENDING + ddo._OP_T_WORLDBANK
    assert ddo._DEEP_COMPLIANCE_BUDGET_S >= others + ddo._OP_T_FINANCIAL_DEEP + ddo._LAYER_TAIL_S, (
        "the financial op would be clamped away by the compliance layer deadline")


def test_rf3131_the_total_budget_can_afford_the_layer():
    assert ddo._dd_total_budget_default("deep") > ddo._DEEP_COMPLIANCE_BUDGET_S + 400


def test_rf3131_standard_mode_is_untouched():
    """Raising standard would push a DD past the 15-min WhatsApp poll window."""
    assert ddo._dd_total_budget_default("standard") == 660.0
    src = module_source(ddo)
    assert '_DEEP_COMPLIANCE_BUDGET_S if mode == "deep"' in src, (
        "the raised layer budget must be gated on deep mode only")


def test_rf3131_op_budget_is_selected_from_the_report_mode():
    """_run_compliance has no `mode` parameter; reading it off the report keeps the
    signature unchanged rather than threading a new argument through."""
    src = function_source(ddo, "_run_compliance")
    assert "_OP_T_FINANCIAL_DEEP" in src
    assert 'getattr(report, "orchestrator_mode", "")' in src
    assert "_fin_op_budget, report.compliance" in src, (
        "the bounded op must receive the mode-selected budget, not the flat one")


def test_rf3131_layer_wrapper_uses_the_same_variable_for_both_bounds():
    """The deadline contextvar and the wait_for must agree, or one silently wins."""
    src = module_source(ddo)
    assert "_clamp(_compliance_budget))" in src
    assert "timeout=_clamp(_compliance_budget)" in src
