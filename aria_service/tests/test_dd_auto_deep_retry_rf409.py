"""R-F409 — auto-escalate INSUFFICIENT_EVIDENCE to deep mode.

Live evidence 2026-05-13 08:18 (WhatsApp): Antonio asked "Aria, run a
new deep DD on adsm-sa.com and investigate the people and network."
ARIA's reply opened with: "The dd_orchestrate engine has now run a
TENTH time on adsm-sa.com and returned the same result: AMBER-LIGHT /
INSUFFICIENT EVIDENCE with zero directors found, zero registry data,
zero procurement footprint, and four of seven layers running silent."

That's a UX failure for MVP launch: the team can't be expected to
remember the magic word "deep" + jurisdiction hint every time. When
standard mode triggers the confidence gate, the orchestrator should
auto-retry ONCE in deep mode before emitting INSUFFICIENT EVIDENCE.

R-F409 ships in two parts:

(a) routes/aria.py dd_orchestrate handler — after the first
    orchestrate_dd call, check `report.confidence_gate_triggered`.
    If True AND mode != "deep" AND not already escalated, re-run
    with mode="deep" and a `_rf409_already_escalated` flag on the
    target dict to prevent infinite recursion. Replace `report`
    with deep result; annotate it `rf409_auto_escalated=True`.

(b) dd_orchestrator.py _assemble_bluf — when emitting INSUFFICIENT
    EVIDENCE, append "R-F409: auto-escalated from X→deep, still
    insufficient" suffix so the operator sees BOTH attempts ran.

These tests pin both halves.
"""
from __future__ import annotations

import inspect
import pathlib


def _src_routes() -> str:
    """Return the CURRENT source of _execute_tool, resolved BY NAME.

    R-F3597 — this was `inspect.getsource(_aria._execute_tool)`, and all eight tests
    in this file failed in the 2026-07-31 full-suite run while passing alone.

    `getsource` takes the line range from the IMPORTED code object and slices the file
    FROM DISK at call time. The run took 77 minutes; a peer agent committed four times
    during it and touched `routes/aria.py`. By the time these tests ran, getsource was
    slicing the NEW file at OLD offsets and returning a DIFFERENT function's body —
    so the assertions were checking text that was never theirs. Nothing leaked; the
    instrument moved under the test.

    Resolving by NAME through the current AST is immune to that: if the function moved,
    it is simply found where it now is.
    """
    import aria_service.routes.aria as _aria
    from ._source_probe import function_source
    return function_source(_aria, "_execute_tool")


def _src_orch() -> str:
    """R-F3597 — was a HARDCODED absolute path ("C:/code/crucix/..."), which reads a
    different checkout than the one under test on any other machine or worktree."""
    from aria_service.intel import dd_orchestrator as _ddo
    from ._source_probe import module_source
    return module_source(_ddo)


# ── 1. Chat handler — wraps orchestrate_dd with retry ────────────

def test_rf409_handler_checks_confidence_gate_after_initial():
    """After the first orchestrate_dd call, the handler must check
    confidence_gate_triggered to decide whether to retry."""
    src = _src_routes()
    assert "R-F409" in src, (
        "R-F409 regression: anchor comment missing -- retry logic not in place."
    )
    assert "confidence_gate_triggered" in src
    assert "rf409_already_escalated" in src.lower(), (
        "R-F409: infinite-recursion guard flag missing."
    )


def test_rf409_handler_skips_when_initial_mode_is_deep():
    """If the user explicitly asked for deep mode, the handler must
    NOT retry -- no value in re-running the same mode."""
    src = _src_routes()
    assert 'mode != "deep"' in src, (
        "R-F409: mode!='deep' guard missing -- deep mode would retry "
        "itself uselessly."
    )


def test_rf409_handler_passes_escalated_flag_to_retry():
    """The retry target dict must include `_rf409_already_escalated`
    so a nested escalation (if anything ever calls back into the
    handler) doesn't double-retry."""
    src = _src_routes()
    assert "_rf409_already_escalated" in src
    assert '"_rf409_already_escalated": True' in src or "_rf409_already_escalated': True" in src


def test_rf409_handler_calls_orchestrate_dd_with_mode_deep():
    """The retry must explicitly pass mode='deep' -- not the original
    mode value. Otherwise the retry runs in the same shallow mode."""
    src = _src_routes()
    assert 'mode="deep"' in src, (
        "R-F409: retry doesn't explicitly pass mode='deep'."
    )


def test_rf409_handler_replaces_report_with_deep_result():
    """The variable `report` must be reassigned to deep_report so
    downstream BLUF + GROUNDING_CHECK + LLM context all see the
    DEEPER attempt."""
    src = _src_routes()
    assert "report = deep_report" in src, (
        "R-F409: deep result not propagated as the canonical report. "
        "Downstream consumers will still see the shallow report."
    )


def test_rf409_handler_annotates_report():
    """The deep report must be marked rf409_auto_escalated=True so the
    BLUF can surface the escalation status."""
    src = _src_routes()
    assert 'setattr(report, "rf409_auto_escalated", True)' in src
    assert 'rf409_initial_mode' in src, (
        "R-F409: initial mode not preserved on the annotated report."
    )


def test_rf409_handler_catches_escalation_exception():
    """If the deep retry itself crashes, the handler must keep the
    shallow report -- operator gets SOMETHING, not nothing."""
    src = _src_routes()
    assert "except Exception as _esc_err" in src, (
        "R-F409: no exception handler around the deep retry -- a "
        "crash there would propagate and lose the shallow result."
    )


# ── 2. BLUF — surfaces the escalation in the headline ────────────

def test_rf409_bluf_appends_escalation_note():
    """When rf409_auto_escalated=True, the BLUF must include the
    "auto-escalated from X→deep, still insufficient" suffix."""
    src = _src_orch()
    # The R-F409 suffix logic lives inside _assemble_bluf,
    # specifically the AMBER_LIGHT + confidence_gate_triggered branch.
    assert "R-F409" in src
    assert "auto-escalated from" in src, (
        "R-F409: BLUF suffix text missing. Operator can't see that "
        "deep mode also ran."
    )
    assert "_rf409_suffix" in src


def test_rf409_bluf_only_appends_when_escalated():
    """The suffix must be conditional on getattr(report,
    rf409_auto_escalated). If the orchestrator was called directly
    in deep mode (no escalation), don't add the note."""
    src = _src_orch()
    idx = src.find("_rf409_suffix")
    assert idx > 0
    block = src[idx:idx + 1500]
    assert 'getattr(report, "rf409_auto_escalated", False)' in block, (
        "R-F409: the conditional check is missing — the BLUF would "
        "say 'auto-escalated' even on a direct deep run."
    )


def test_rf409_bluf_says_operator_action_needed():
    """When auto-escalation still fails, the BLUF must explicitly
    list what the operator should supply (jurisdiction / website /
    name). Otherwise the team is in the same loop as before R-F409."""
    src = _src_orch()
    idx = src.find("R-F409")
    block = src[idx:idx + 1500]
    assert "jurisdiction_iso2" in block, (
        "R-F409: BLUF doesn't tell operator what to supply. The whole "
        "point of escalation is to surface the data gap clearly."
    )


# ── 3. Regression — no recursive call, no double escalation ──────

def test_rf409_handler_only_one_retry():
    """The handler must not call orchestrate_dd more than 2 times
    total (first run + one retry). Pin the count so a future refactor
    can't accidentally add a third call."""
    src = _src_routes()
    # Count orchestrate_dd calls in the full _execute_tool function.
    # Should be exactly 2 (initial + 1 retry). Uses the full function
    # body so the count doesn't break when code is added above/below
    # the R-F409 block (R-F1956 structural fix).
    n_calls = src.count("dd_orchestrator.orchestrate_dd(")
    assert n_calls == 2, (
        f"R-F409: expected exactly 2 orchestrate_dd calls in the "
        f"handler (initial + 1 retry). Got {n_calls}. Infinite "
        f"recursion risk or missing retry."
    )
